from __future__ import annotations

import re
from typing import Any

from app.domain.block import BlockKind, PreservedBlock
from app.domain.citation import CitationMode, CitationNode
from app.domain.document import CitationRef, Document, Paragraph, Section, TextRun
from app.domain.hashing import normalize_text

PANDOC_API_VERSION = [1, 23, 1]

_MODES = {
    CitationMode.NORMAL: "NormalCitation",
    CitationMode.AUTHOR_IN_TEXT: "AuthorInText",
    CitationMode.SUPPRESS_AUTHOR: "SuppressAuthor",
}


_MODES_BACK = {pandoc: mode for mode, pandoc in _MODES.items()}


def build(document: Document, *, nocite_reference_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = []

    for section in document.sections:
        blocks.extend(_section(section, document))

    trailing = [
        document.blocks[block_id]
        for block_id in document.floating_block_ids
        if block_id in document.blocks
    ]
    if trailing:
        blocks.append(_header(1, "floats", "Figures and Tables"))
        for block in sorted(trailing, key=lambda item: item.order_index):
            blocks.extend(_block(block))

    return {
        "pandoc-api-version": PANDOC_API_VERSION,
        "meta": _meta(document, nocite_reference_ids),
        "blocks": blocks,
    }


def _meta(document: Document, nocite_reference_ids: tuple[str, ...]) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if document.title:
        meta["title"] = {"t": "MetaString", "c": document.title}
    if document.authors:
        meta["author"] = {
            "t": "MetaList",
            "c": [
                {"t": "MetaInlines", "c": _inlines_from_text(author.name)}
                for author in document.authors
            ],
        }
    if document.abstract:
        meta["abstract"] = {
            "t": "MetaBlocks",
            "c": [_para(_inlines_from_text(document.abstract))],
        }
    if nocite_reference_ids:
        meta["nocite"] = {"t": "MetaInlines", "c": [_nocite(nocite_reference_ids)]}
    return meta


def _nocite(reference_ids: tuple[str, ...]) -> dict[str, Any]:
    items = [
        {
            "citationId": reference_id,
            "citationPrefix": [],
            "citationSuffix": [],
            "citationMode": {"t": "NormalCitation"},
            "citationNoteNum": 0,
            "citationHash": 0,
        }
        for reference_id in reference_ids
    ]
    literal = _inlines_from_text("[" + ", ".join(f"@{item}" for item in reference_ids) + "]")
    return {"t": "Cite", "c": [items, literal]}


def _section(section: Section, document: Document) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    if section.title:
        blocks.append(_header(min(section.level + 1, 6), section.id, section.title))
    for node in section.body:
        if isinstance(node, Paragraph):
            blocks.append(_div(node.id, [_para(_inlines(node, document))]))
        else:
            block = document.blocks.get(node.block_id)
            if block is not None:
                blocks.extend(_block(block))
    return blocks


def _inlines(paragraph: Paragraph, document: Document) -> list[dict[str, Any]]:
    inlines: list[dict[str, Any]] = []
    for node in paragraph.inlines:
        if isinstance(node, TextRun):
            inlines.extend(_inlines_from_text(node.text))
        elif isinstance(node, CitationRef):
            citation = document.citations.get(node.citation_id)
            if citation is not None:
                inlines.append(_cite(citation))
    return inlines


def _cite(node: CitationNode) -> dict[str, Any]:
    items = [
        {
            "citationId": item.reference_id or "",
            "citationPrefix": _inlines_from_text(item.prefix) if item.prefix else [],
            "citationSuffix": _inlines_from_text(item.encoded_suffix()),
            "citationMode": {"t": _MODES[item.mode]},
            "citationNoteNum": 0,
            "citationHash": 0,
        }
        for item in node.items
        if item.reference_id
    ]
    if not items:
        return _span(node.id, _inlines_from_text(node.raw_marker or ""))

    cite = {"t": "Cite", "c": [items, _inlines_from_text(node.raw_marker or "")]}
    return _span(node.id, [cite])


def _inlines_from_text(text: str) -> list[dict[str, Any]]:
    inlines: list[dict[str, Any]] = []
    for index, line in enumerate(text.split("\n")):
        if index:
            inlines.append({"t": "SoftBreak"})
        first = True
        for word in line.split(" "):
            if not first:
                inlines.append({"t": "Space"})
            first = False
            if word:
                inlines.append({"t": "Str", "c": word})
    return inlines


def _block(block: PreservedBlock) -> list[dict[str, Any]]:
    body: list[dict[str, Any]] = []
    caption = block.caption or block.label

    if block.source_asset:
        if block.kind is BlockKind.FIGURE:
            body.append(_figure(block.source_asset, _figure_caption(block)))
        else:
            body.append(_para([_image(block.source_asset, _asset_alt(block))]))
            if block.kind is BlockKind.TABLE:
                table_caption = _table_caption(block)
                if table_caption:
                    body.append(_para([{"t": "Emph", "c": _inlines_from_text(table_caption)}]))
    elif block.kind is BlockKind.TABLE and block.table_rows:
        body.append(_table(block.table_rows, caption))
    elif block.kind is BlockKind.FORMULA:
        body.append(
            _para(
                [
                    {
                        "t": "Emph",
                        "c": _inlines_from_text(
                            "[Formula source crop unavailable; equation omitted from this draft.]"
                        ),
                    }
                ]
            )
        )
    elif block.kind is BlockKind.TABLE and block.normalized_content:
        body.append(
            _para(
                [
                    {
                        "t": "Emph",
                        "c": _inlines_from_text(
                            "[Table structure unavailable; source table omitted from this draft.]"
                        ),
                    }
                ]
            )
        )
    elif block.normalized_content:
        body.append(_para(_inlines_from_text(block.normalized_content)))

    if (
        caption
        and not block.source_asset
        and not (block.kind is BlockKind.TABLE and block.table_rows)
    ):
        body.append(_para([{"t": "Emph", "c": _inlines_from_text(caption)}]))
    if block.missing_asset:
        body.append(
            _para(
                [
                    {
                        "t": "Emph",
                        "c": _inlines_from_text(
                            "[Figure image not reproducible from the source PDF.]"
                        ),
                    }
                ]
            )
        )
    if not body:
        body.append(_para(_inlines_from_text(f"[{block.kind.value.title()} omitted.]")))

    return [_div(block.id, body, classes=[block.kind.value.lower()])]


def _div(
    identifier: str, blocks: list[dict[str, Any]], classes: list[str] | None = None
) -> dict[str, Any]:
    return {"t": "Div", "c": [[identifier, classes or [], []], blocks]}


def _span(identifier: str, inlines: list[dict[str, Any]]) -> dict[str, Any]:
    return {"t": "Span", "c": [[identifier, [], []], inlines]}


def _para(inlines: list[dict[str, Any]]) -> dict[str, Any]:
    return {"t": "Para", "c": inlines}


def _header(level: int, identifier: str, text: str) -> dict[str, Any]:
    return {"t": "Header", "c": [level, [identifier, [], []], _inlines_from_text(text)]}


def _code(text: str) -> dict[str, Any]:
    return {"t": "CodeBlock", "c": [["", [], []], text]}


def _asset_alt(block: PreservedBlock) -> str:
    return f"{block.kind.value.title()} source crop"


def _image(path: str, alt: str) -> dict[str, Any]:
    return {
        "t": "Image",
        "c": [["", [], []], _inlines_from_text(alt), [path, ""]],
    }


def _figure_caption(block: PreservedBlock) -> str:
    caption = block.caption or block.label or _asset_alt(block)
    return re.sub(r"^Figure\s+\d+\s*[:.]\s*", "", caption, flags=re.IGNORECASE)


def _table_caption(block: PreservedBlock) -> str:
    caption = block.caption or ""
    if not caption:
        return f"Table {block.label}." if block.label else ""
    if re.match(r"^Table\b", caption, flags=re.IGNORECASE):
        return caption
    return f"Table {block.label}. {caption}" if block.label else f"Table. {caption}"


def _figure(path: str, alt: str) -> dict[str, Any]:
    image = _image(path, alt)
    return {
        "t": "Figure",
        "c": [
            ["", [], []],
            [None, [_para(_inlines_from_text(alt))]],
            [_para([image])],
        ],
    }


def _table(rows: tuple[tuple[str, ...], ...], caption: str | None) -> dict[str, Any]:
    width = max(len(row) for row in rows)
    normalized = [row + ("",) * (width - len(row)) for row in rows]

    def cell(text: str) -> list[Any]:
        return [
            ["", [], []],
            {"t": "AlignDefault"},
            1,
            1,
            [_plain_block(_inlines_from_text(text))],
        ]

    def row(values: tuple[str, ...]) -> list[Any]:
        return [["", [], []], [cell(value) for value in values]]

    head = [["", [], []], [row(normalized[0])]]
    bodies = [[["", [], []], 0, [], [row(values) for values in normalized[1:]]]]
    foot = [["", [], []], []]
    return {
        "t": "Table",
        "c": [
            ["", [], []],
            [None, [_plain_block(_inlines_from_text(caption))] if caption else []],
            [[{"t": "AlignDefault"}, {"t": "ColWidthDefault"}] for _ in range(width)],
            head,
            bodies,
            foot,
        ],
    }


def _plain_block(inlines: list[dict[str, Any]]) -> dict[str, Any]:
    return {"t": "Plain", "c": inlines}


def citation_signatures(ir: dict[str, Any]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for span_id, cite in _walk_cites(ir.get("blocks", [])):
        found[span_id] = [
            "|".join(
                [
                    item.get("citationId", ""),
                    _mode_back(item.get("citationMode", {}).get("t", "")),
                    _plain(item.get("citationPrefix", [])),
                    _plain(item.get("citationSuffix", [])),
                ]
            )
            for item in cite
        ]
    return found


def _mode_back(name: str) -> str:
    mode = _MODES_BACK.get(name)
    return mode.value if mode else name


def _walk_cites(node: Any, span_id: str = "") -> list[tuple[str, list[dict[str, Any]]]]:
    found: list[tuple[str, list[dict[str, Any]]]] = []
    if isinstance(node, dict):
        kind = node.get("t")
        if kind == "Span":
            attributes, inlines = node["c"]
            return _walk_cites(inlines, attributes[0])
        if kind == "Cite":
            found.append((span_id, node["c"][0]))
            return found
        for value in node.values():
            found.extend(_walk_cites(value, span_id))
    elif isinstance(node, list):
        for value in node:
            found.extend(_walk_cites(value, span_id))
    return found


def _plain(inlines: Any) -> str:
    if not isinstance(inlines, list):
        return ""
    parts: list[str] = []
    for node in inlines:
        if not isinstance(node, dict):
            continue
        if node.get("t") == "Str":
            parts.append(str(node.get("c", "")))
        elif node.get("t") in {"Space", "SoftBreak"}:
            parts.append(" ")
        elif isinstance(node.get("c"), list):
            parts.append(_plain(node["c"]))
    return normalize_text("".join(parts))
