from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from lxml import etree

from app.domain.block import (
    BlockKind,
    BlockParseStatus,
    FidelityStatus,
    PreservedBlock,
    SourceBox,
)
from app.domain.citation import (
    CitationItem,
    CitationMode,
    CitationNode,
    LocatorLabel,
    SemanticParseStatus,
)
from app.domain.document import (
    Author,
    BlockRef,
    BodyNode,
    CitationRef,
    Document,
    InlineNode,
    Paragraph,
    Section,
    TextRun,
)
from app.domain.ids import (
    Origin,
    block_id,
    citation_id,
    paragraph_id,
    reference_id,
    section_id,
)
from app.domain.reference import NormalizationStatus, ReferenceRecord
from app.services.citations.csl import biblstruct_xml_id, raw_reference_text, to_csl
from app.services.parser.segmenter import SEGMENTER_VERSION
from app.services.parser.xml_safety import NS, local_name, text_of


class CitationFamily(StrEnum):
    NUMERIC = "NUMERIC"
    AUTHOR_YEAR = "AUTHOR_YEAR"
    UNKNOWN = "UNKNOWN"


_NUMERIC_MARKER = re.compile(r"^[\[\(]?\s*\d+\s*[\],;\)]?\s*$")
_YEAR_MARKER = re.compile(r"(1[6-9]\d{2}|20\d{2})")
_RANGE = re.compile(r"^[\[\(]?\s*(\d+)\s*[-–—]\s*(\d+)\s*[\]\)]?\s*$")


_PREFIX_WORDS = re.compile(
    r"^(see(\s+also)?|e\.?g\.?|i\.?e\.?|cf\.?|compare|reviewed\s+in|following|after)"
    r"[,\s]*$",
    re.IGNORECASE,
)

_LOCATOR = re.compile(
    r"\b(pp?|chaps?|chapters?|secs?|sections?|figs?|tbls?|tables?|eqs?|equations?)\."
    r"\s*(\d+(?:\s*[-–—]\s*\d+)?)",
    re.IGNORECASE,
)

_LOCATOR_LABELS: dict[str, LocatorLabel] = {
    "p": LocatorLabel.PAGE,
    "pp": LocatorLabel.PAGE,
    "chap": LocatorLabel.CHAPTER,
    "chaps": LocatorLabel.CHAPTER,
    "chapter": LocatorLabel.CHAPTER,
    "chapters": LocatorLabel.CHAPTER,
    "sec": LocatorLabel.SECTION,
    "secs": LocatorLabel.SECTION,
    "section": LocatorLabel.SECTION,
    "sections": LocatorLabel.SECTION,
    "fig": LocatorLabel.FIGURE,
    "figs": LocatorLabel.FIGURE,
    "tbl": LocatorLabel.TABLE,
    "tbls": LocatorLabel.TABLE,
    "table": LocatorLabel.TABLE,
    "tables": LocatorLabel.TABLE,
    "eq": LocatorLabel.EQUATION,
    "eqs": LocatorLabel.EQUATION,
    "equation": LocatorLabel.EQUATION,
    "equations": LocatorLabel.EQUATION,
}

_AUTHOR_IN_TEXT = re.compile(r"\(\s*\d{4}[a-z]?\s*\)\s*[.,;]?\s*$")
_SUPPRESS_AUTHOR = re.compile(r"^\(\s*\d{4}[a-z]?\s*\)\s*$")


_ABSORB_LIMIT = 48
_ABSORB_TAIL = re.compile(r"([\(\[])([^\(\)\[\]]*)$")


_MAX_RANGE_SPAN = 30


@dataclass(frozen=True)
class LinkageEvidence:
    citation_id: str
    item_index: int
    marker_fragment: str
    xml_target: str | None
    reference_id: str | None
    bibliography_order: int | None


@dataclass
class MappingDiagnostics:
    family: CitationFamily = CitationFamily.UNKNOWN
    tei_bibr_refs: int = 0
    tei_bibl_entries: int = 0
    clusters: int = 0
    absorbed_openers: int = 0
    dropped_author_debris: int = 0
    ranges_expanded: int = 0
    modifiers_seen: int = 0
    modifiers_attributed: int = 0

    out_of_scope_refs: int = 0
    linkage: list[LinkageEvidence] = field(default_factory=list)


@dataclass(frozen=True)
class MappedDocument:
    document: Document
    diagnostics: MappingDiagnostics


def map_tei(root: etree._Element) -> MappedDocument:
    diagnostics = MappingDiagnostics()
    references, by_xml_id, by_order = _map_references(root, diagnostics)
    state = _MapperState(
        references_by_xml_id=by_xml_id,
        references_by_order=by_order,
        family=_detect_family(root),
        diagnostics=diagnostics,
    )
    diagnostics.family = state.family

    sections = _map_sections(root, state)
    floating = _map_floating_blocks(root, state)

    document = Document(
        title=_first_text(root, ".//tei:fileDesc//tei:titleStmt/tei:title"),
        authors=_authors(root),
        abstract=_abstract(root),
        sections=tuple(sections),
        citations=state.citations,
        blocks=state.blocks,
        references=references,
        floating_block_ids=tuple(floating),
        segmenter_version=SEGMENTER_VERSION,
    )
    diagnostics.tei_bibr_refs = len(root.findall(".//tei:ref[@type='bibr']", namespaces=NS))
    mapped_refs = sum(len(node.items) for node in state.citations.values())
    diagnostics.out_of_scope_refs = max(0, diagnostics.tei_bibr_refs - mapped_refs)
    return MappedDocument(document=document, diagnostics=diagnostics)


@dataclass
class _MapperState:
    references_by_xml_id: dict[str, ReferenceRecord]
    references_by_order: dict[int, ReferenceRecord]
    family: CitationFamily
    diagnostics: MappingDiagnostics
    citations: dict[str, CitationNode] = field(default_factory=dict)
    blocks: dict[str, PreservedBlock] = field(default_factory=dict)
    citation_ordinal: int = 0
    block_ordinal: int = 0

    def next_citation_id(self) -> str:
        self.citation_ordinal += 1
        return citation_id(Origin.IMPORTED, self.citation_ordinal)

    def next_block_id(self, kind: BlockKind) -> str:
        self.block_ordinal += 1
        return block_id(kind.value, self.block_ordinal)


def _map_references(
    root: etree._Element, diagnostics: MappingDiagnostics
) -> tuple[tuple[ReferenceRecord, ...], dict[str, ReferenceRecord], dict[int, ReferenceRecord]]:
    entries = root.findall(".//tei:listBibl/tei:biblStruct", namespaces=NS)
    diagnostics.tei_bibl_entries = len(entries)

    records: list[ReferenceRecord] = []
    by_xml_id: dict[str, ReferenceRecord] = {}
    by_order: dict[int, ReferenceRecord] = {}

    for position, entry in enumerate(entries, start=1):
        identifier = reference_id(Origin.IMPORTED, position)
        csl, status = to_csl(entry, identifier)
        record = ReferenceRecord(
            id=identifier,
            csl=csl,
            raw_text=raw_reference_text(entry),
            bibliography_order=position,
            normalization_status=status,
        )
        records.append(record)
        by_order[position] = record
        xml_id = biblstruct_xml_id(entry)
        if xml_id:
            by_xml_id[xml_id] = record

    return tuple(records), by_xml_id, by_order


def _detect_family(root: etree._Element) -> CitationFamily:
    numeric = 0
    author_year = 0
    for ref in root.findall(".//tei:ref[@type='bibr']", namespaces=NS):
        marker = text_of(ref)
        if not marker:
            continue
        if _NUMERIC_MARKER.match(marker):
            numeric += 1
        elif _YEAR_MARKER.search(marker):
            author_year += 1
    if numeric == 0 and author_year == 0:
        return CitationFamily.UNKNOWN

    total = numeric + author_year
    if numeric >= total * 0.8:
        return CitationFamily.NUMERIC
    if author_year >= total * 0.8:
        return CitationFamily.AUTHOR_YEAR
    return CitationFamily.UNKNOWN


def _abstract(root: etree._Element) -> str:
    node = root.find(".//tei:profileDesc/tei:abstract", namespaces=NS)
    return text_of(node) if node is not None else ""


def _authors(root: etree._Element) -> tuple[Author, ...]:
    candidates = root.findall(".//tei:fileDesc/tei:titleStmt/tei:author", namespaces=NS)
    if not candidates:
        candidates = root.findall(
            ".//tei:fileDesc/tei:sourceDesc/tei:biblStruct/tei:analytic/tei:author",
            namespaces=NS,
        )

    authors: list[Author] = []
    seen: set[str] = set()
    for author in candidates:
        pers_name = author.find("tei:persName", namespaces=NS)
        if pers_name is None:
            pers_name = author.find(".//tei:persName", namespaces=NS)

        if pers_name is not None:
            forenames = [
                text_of(node)
                for node in pers_name.findall("tei:forename", namespaces=NS)
                if text_of(node)
            ]
            surname_node = pers_name.find("tei:surname", namespaces=NS)
            surname = text_of(surname_node) if surname_node is not None else ""
            name = " ".join([*forenames, surname]).strip()
        else:
            name = text_of(author)

        if not name:
            continue
        key = " ".join(name.casefold().split())
        if key in seen:
            continue
        seen.add(key)
        affiliations = tuple(
            text_of(node)
            for node in author.findall("tei:affiliation", namespaces=NS)
            if text_of(node)
        )
        authors.append(Author(name=name, affiliations=affiliations))
    return tuple(authors)


def _first_text(root: etree._Element, path: str) -> str:
    node = root.find(path, namespaces=NS)
    return text_of(node) if node is not None else ""


def _map_sections(root: etree._Element, state: _MapperState) -> list[Section]:
    body = root.find(".//tei:text/tei:body", namespaces=NS)
    back = root.find(".//tei:text/tei:back", namespaces=NS)

    containers: list[etree._Element] = []
    if body is not None:
        containers.extend(body.findall("tei:div", namespaces=NS))
    if back is not None:
        for div in back.findall("tei:div", namespaces=NS):
            if div.get("type") != "references":
                containers.append(div)

    sections: list[Section] = []
    for container in containers:
        _walk_div(container, state, sections, level=1)
    return sections


def _walk_div(
    div: etree._Element, state: _MapperState, sections: list[Section], *, level: int
) -> None:
    ordinal = len(sections) + 1
    identifier = section_id(ordinal)
    head = div.find("tei:head", namespaces=NS)
    title = text_of(head) if head is not None else ""

    body: list[BodyNode] = []
    nested: list[etree._Element] = []
    paragraph_ordinal = 0

    for child in div:
        name = local_name(child)
        if name == "head":
            continue
        if name == "div":
            nested.append(child)
        elif name == "p":
            paragraph_ordinal += 1
            body.append(
                _map_paragraph(
                    child,
                    state,
                    identifier=paragraph_id(ordinal, paragraph_ordinal),
                    order_index=len(body),
                )
            )
        elif name in {"formula", "figure", "table", "list", "note"}:
            block = _map_block(child, state)
            body.append(BlockRef(block_id=block.id, order_index=len(body)))

    sections.append(
        Section(
            id=identifier,
            title=title or _fallback_title(div, ordinal),
            level=level,
            order_index=ordinal - 1,
            body=tuple(body),
        )
    )
    for child_div in nested:
        _walk_div(child_div, state, sections, level=level + 1)


def _fallback_title(div: etree._Element, ordinal: int) -> str:
    kind = div.get("type")
    if kind:
        return kind.replace("_", " ").title()
    return f"Section {ordinal}"


def _map_paragraph(
    element: etree._Element, state: _MapperState, *, identifier: str, order_index: int
) -> Paragraph:
    inlines: list[InlineNode] = []
    pending: list[str] = [element.text or ""]

    children = list(element)
    index = 0
    while index < len(children):
        child = children[index]
        if _is_bibr(child):
            last = index
            while (
                _is_blank(children[last].tail)
                and last + 1 < len(children)
                and _is_bibr(children[last + 1])
            ):
                last += 1
            node = _map_cluster(children[index : last + 1], state, pending)
            _flush(pending, inlines)
            inlines.append(CitationRef(citation_id=node.id))
            pending.append(children[last].tail or "")
            index = last + 1
        else:
            pending.append(_inline_text(child))
            pending.append(child.tail or "")
            index += 1

    _flush(pending, inlines)
    return Paragraph(id=identifier, order_index=order_index, inlines=tuple(inlines))


def _inline_text(child: etree._Element) -> str:
    return text_of(child)


def _flush(pending: list[str], inlines: list[InlineNode]) -> None:
    text = "".join(pending)
    pending.clear()
    if text:
        inlines.append(TextRun(text=text))


def _is_bibr(element: etree._Element) -> bool:
    return local_name(element) == "ref" and element.get("type") == "bibr"


def _is_blank(value: str | None) -> bool:
    return not (value or "").strip()


def _map_cluster(
    refs: list[etree._Element], state: _MapperState, pending: list[str]
) -> CitationNode:
    state.diagnostics.clusters += 1
    fragments = [text_of(ref) for ref in refs]
    marker = _join_fragments(refs, fragments)
    opener, inner = _absorb_opener(pending, marker, state)
    if opener:
        marker = f"{opener}{inner}{marker}"

    mode = _detect_mode(marker)
    items, status = _build_items(refs, fragments, marker, mode, state)
    status = _apply_modifiers(items, marker, inner, status, state)

    identifier = state.next_citation_id()
    node = CitationNode(
        id=identifier,
        items=tuple(items),
        raw_marker=marker,
        semantic_parse_status=status,
    )
    state.citations[identifier] = node

    padded = _fragments_for(items, fragments)
    for position, (item, fragment) in enumerate(zip(items, padded, strict=True)):
        reference = (
            state.references_by_xml_id.get(_target_of(refs[position]) or "")
            if position < len(refs)
            else None
        )
        state.diagnostics.linkage.append(
            LinkageEvidence(
                citation_id=identifier,
                item_index=position,
                marker_fragment=fragment,
                xml_target=_target_of(refs[position]) if position < len(refs) else None,
                reference_id=item.reference_id,
                bibliography_order=reference.bibliography_order if reference else None,
            )
        )
    return node


def _fragments_for(items: list[CitationItem], fragments: list[str]) -> list[str]:
    if len(fragments) >= len(items):
        return fragments[: len(items)]
    return fragments + [""] * (len(items) - len(fragments))


def _join_fragments(refs: list[etree._Element], fragments: list[str]) -> str:
    parts: list[str] = []
    for position, fragment in enumerate(fragments):
        if position > 0:
            tail = refs[position - 1].tail or ""
            if tail.strip():
                parts.append(tail)
            elif parts and parts[-1].rstrip().endswith((",", ";")):
                parts.append(" ")
        parts.append(fragment)
    return "".join(parts).strip()


def _absorb_opener(pending: list[str], marker: str, state: _MapperState) -> tuple[str, str]:
    prose = "".join(pending)
    match = _ABSORB_TAIL.search(prose)
    if not match:
        return "", ""

    opener, inner = match.group(1), match.group(2)
    closer = ")" if opener == "(" else "]"
    if not marker.rstrip().endswith((closer, f"{closer}.", f"{closer},")):
        return "", ""
    if len(inner) > _ABSORB_LIMIT or any(char in inner for char in ".!?"):
        return "", ""

    pending.clear()
    pending.append(prose[: match.start()])
    state.diagnostics.absorbed_openers += 1
    return opener, inner


def _target_of(ref: etree._Element) -> str | None:
    target = ref.get("target")
    if not target:
        return None
    return str(target).lstrip("#") or None


def _detect_mode(marker: str) -> CitationMode:
    if _SUPPRESS_AUTHOR.match(marker):
        return CitationMode.SUPPRESS_AUTHOR
    if not marker.startswith(("(", "[")) and _AUTHOR_IN_TEXT.search(marker):
        return CitationMode.AUTHOR_IN_TEXT
    return CitationMode.NORMAL


def _build_items(
    refs: list[etree._Element],
    fragments: list[str],
    marker: str,
    mode: CitationMode,
    state: _MapperState,
) -> tuple[list[CitationItem], SemanticParseStatus]:
    items: list[CitationItem] = []
    unlinked = 0

    for ref, fragment in zip(refs, fragments, strict=True):
        target = _target_of(ref)
        record = state.references_by_xml_id.get(target) if target else None
        if record is None:
            unlinked += 1
        items.append(CitationItem(reference_id=record.id if record else None, mode=mode))
        expanded = _expand_range(fragment, record, mode, state)
        items.extend(expanded)

    if unlinked and len(refs) == 1 and len(items) == 1:
        return items, SemanticParseStatus.RAW_ONLY
    if unlinked:
        return items, SemanticParseStatus.RAW_ONLY
    if not items:
        return items, SemanticParseStatus.RAW_ONLY
    return items, SemanticParseStatus.STRUCTURED


def _expand_range(
    fragment: str,
    record: ReferenceRecord | None,
    mode: CitationMode,
    state: _MapperState,
) -> list[CitationItem]:
    if state.family is not CitationFamily.NUMERIC or record is None:
        return []
    match = _RANGE.match(fragment.strip())
    if not match:
        return []

    start, end = int(match.group(1)), int(match.group(2))
    if end <= start or end - start > _MAX_RANGE_SPAN:
        return []
    if start != record.bibliography_order:
        return []
    interior = [state.references_by_order.get(order) for order in range(start + 1, end + 1)]
    if any(entry is None for entry in interior):
        return []

    state.diagnostics.ranges_expanded += 1
    return [
        CitationItem(reference_id=entry.id, mode=mode) for entry in interior if entry is not None
    ]


def _apply_modifiers(
    items: list[CitationItem],
    marker: str,
    absorbed: str,
    status: SemanticParseStatus,
    state: _MapperState,
) -> SemanticParseStatus:
    if status is SemanticParseStatus.RAW_ONLY or not items:
        return status

    locator = _LOCATOR.search(marker)
    prefix = absorbed.strip()
    degraded = False

    if locator:
        state.diagnostics.modifiers_seen += 1
        if len(items) == 1:
            keyword = locator.group(1).lower().rstrip(".")
            label = _LOCATOR_LABELS.get(keyword, LocatorLabel.PAGE)
            items[0] = items[0].model_copy(
                update={
                    "locator": " ".join(locator.group(2).split()),
                    "locator_label": label,
                }
            )
            state.diagnostics.modifiers_attributed += 1
        else:
            degraded = True

    if prefix:
        if _PREFIX_WORDS.match(prefix):
            state.diagnostics.modifiers_seen += 1
            if len(items) == 1:
                items[0] = items[0].model_copy(update={"prefix": prefix.rstrip(", ")})
                state.diagnostics.modifiers_attributed += 1
            else:
                degraded = True
        elif state.family is CitationFamily.AUTHOR_YEAR:
            state.diagnostics.dropped_author_debris += 1
        else:
            state.diagnostics.modifiers_seen += 1
            degraded = True

    return SemanticParseStatus.PARTIAL_MODIFIERS if degraded else status


def _map_floating_blocks(root: etree._Element, state: _MapperState) -> list[str]:
    body = root.find(".//tei:text/tei:body", namespaces=NS)
    if body is None:
        return []
    floating: list[str] = []
    for child in body:
        if local_name(child) in {"figure", "table", "note", "formula"}:
            floating.append(_map_block(child, state).id)
    return floating


def _map_block(element: etree._Element, state: _MapperState) -> PreservedBlock:
    kind = _block_kind(element)
    identifier = state.next_block_id(kind)
    raw = etree.tostring(element, encoding="unicode")

    head = element.find("tei:head", namespaces=NS)
    label = element.find("tei:label", namespaces=NS)
    description = element.find("tei:figDesc", namespaces=NS)
    caption = text_of(description) if description is not None else None
    if caption is None and head is not None:
        caption = text_of(head)

    content, parse_status, fidelity, missing_asset = _block_content(element, kind)
    table_rows = _extract_table_rows(element) if kind is BlockKind.TABLE else ()
    label_node = label if label is not None else head
    block = PreservedBlock(
        id=identifier,
        kind=kind,
        order_index=state.block_ordinal,
        label=text_of(label_node) if label_node is not None else None,
        caption=caption,
        normalized_content=content,
        raw_source=raw,
        parse_status=parse_status,
        fidelity_status=fidelity,
        missing_asset=missing_asset,
        source_boxes=_source_boxes(element),
        table_rows=table_rows,
    )
    state.blocks[identifier] = block
    return block


_SIMPLE_BLOCK_KINDS = {
    "formula": BlockKind.FORMULA,
    "note": BlockKind.NOTE,
    "list": BlockKind.LIST,
    "table": BlockKind.TABLE,
}


def _block_kind(element: etree._Element) -> BlockKind:
    name = local_name(element)
    simple = _SIMPLE_BLOCK_KINDS.get(name)
    if simple is not None:
        return simple
    if name == "figure":
        if element.get("type") == "table" or element.find("tei:table", namespaces=NS) is not None:
            return BlockKind.TABLE
        return BlockKind.FIGURE
    return BlockKind.UNKNOWN


def _source_boxes(element: etree._Element) -> tuple[SourceBox, ...]:
    raw_values: list[str] = []
    element_coords = element.get("coords")
    if element_coords:
        raw_values.append(element_coords)
    for graphic in element.findall(".//tei:graphic", namespaces=NS):
        graphic_coords = graphic.get("coords")
        if graphic_coords:
            raw_values.append(graphic_coords)
    if not raw_values:
        return ()
    boxes: list[SourceBox] = []
    for raw in raw_values:
        for part in raw.split(";"):
            values = [value.strip() for value in part.split(",")]
            if len(values) != 5:
                continue
            try:
                page, x, y, width, height = values
                parsed = SourceBox(
                    page=int(page),
                    x=float(x),
                    y=float(y),
                    width=float(width),
                    height=float(height),
                )
            except (TypeError, ValueError):
                continue
            if parsed.page < 1 or parsed.width <= 0 or parsed.height <= 0:
                continue
            if parsed not in boxes:
                boxes.append(parsed)
    return tuple(boxes)


_UNRECOVERABLE = ("", BlockParseStatus.RAW_ONLY, FidelityStatus.UNRENDERABLE, False)


def _block_content(
    element: etree._Element, kind: BlockKind
) -> tuple[str, BlockParseStatus, FidelityStatus, bool]:
    if kind is BlockKind.TABLE:
        return _table_content(element)
    if kind is BlockKind.FIGURE:
        return _figure_content(element)
    text = text_of(element)
    if text:
        return text, BlockParseStatus.STRUCTURED, FidelityStatus.CONVERTED, False
    return _UNRECOVERABLE


def _table_content(
    element: etree._Element,
) -> tuple[str, BlockParseStatus, FidelityStatus, bool]:
    if local_name(element) == "table":
        table: etree._Element | None = element
    else:
        table = element.find("tei:table", namespaces=NS)
    rendered = _render_table(table) if table is not None else ""
    if rendered:
        return rendered, BlockParseStatus.STRUCTURED, FidelityStatus.CONVERTED, False
    return _UNRECOVERABLE


def _extract_table_rows(element: etree._Element) -> tuple[tuple[str, ...], ...]:
    table = element if local_name(element) == "table" else element.find("tei:table", namespaces=NS)
    if table is None:
        return ()
    rows: list[tuple[str, ...]] = []
    for row in table.findall("tei:row", namespaces=NS):
        cells = tuple(text_of(cell) for cell in row.findall("tei:cell", namespaces=NS))
        if cells:
            rows.append(cells)
    return tuple(rows)


def _figure_content(
    element: etree._Element,
) -> tuple[str, BlockParseStatus, FidelityStatus, bool]:
    if element.find("tei:graphic", namespaces=NS) is not None:
        return "", BlockParseStatus.PARTIAL, FidelityStatus.UNRENDERABLE, True
    description = element.find("tei:figDesc", namespaces=NS)
    if description is not None:
        return text_of(description), BlockParseStatus.PARTIAL, FidelityStatus.CONVERTED, False
    return _UNRECOVERABLE


def _render_table(table: etree._Element) -> str:
    rows: list[list[str]] = []
    for row in table.findall("tei:row", namespaces=NS):
        cells = [text_of(cell) for cell in row.findall("tei:cell", namespaces=NS)]
        if cells:
            rows.append(cells)
    if not rows:
        return ""

    width = max(len(row) for row in rows)
    padded = [row + [""] * (width - len(row)) for row in rows]
    lines = ["| " + " | ".join(padded[0]) + " |", "|" + "---|" * width]
    lines.extend("| " + " | ".join(row) + " |" for row in padded[1:])
    return "\n".join(lines)


__all__ = [
    "CitationFamily",
    "LinkageEvidence",
    "MappedDocument",
    "MappingDiagnostics",
    "map_tei",
]


def normalization_counts(document: Document) -> dict[NormalizationStatus, int]:
    counts = dict.fromkeys(NormalizationStatus, 0)
    for reference in document.references:
        counts[reference.normalization_status] += 1
    return counts
