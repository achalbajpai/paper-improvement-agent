from __future__ import annotations

from app.domain.block import PreservedBlock
from app.domain.citation import CitationNode
from app.domain.delta import Change, ChangeKind, ComputedEditDelta
from app.domain.document import Document, Paragraph
from app.domain.hashing import canonical_sha256, text_sha256


def compute_delta(before: Document, after: Document) -> ComputedEditDelta:
    changes: list[Change] = []
    changes.extend(_section_changes(before, after))
    changes.extend(_paragraph_changes(before, after))
    changes.extend(_citation_changes(before, after))
    changes.extend(_reference_changes(before, after))
    changes.extend(_block_changes(before, after))
    touched = {
        change.subject_id
        for change in changes
        if change.kind
        in (
            ChangeKind.PARAGRAPH_TEXT_CHANGED,
            ChangeKind.PARAGRAPH_ADDED,
            ChangeKind.PARAGRAPH_REMOVED,
        )
    }
    return ComputedEditDelta(
        changes=tuple(changes),
        words_before=before.word_count(),
        words_after=after.word_count(),
        scope_words_before=_words_in(before, touched),
        scope_words_after=_words_in(after, touched),
    )


def _words_in(document: Document, paragraph_ids: set[str]) -> int:
    return sum(
        paragraph.word_count()
        for paragraph in document.paragraphs()
        if paragraph.id in paragraph_ids
    )


def _section_changes(before: Document, after: Document) -> list[Change]:
    after_ids = {section.id for section in after.sections}
    return [
        Change(
            kind=ChangeKind.SECTION_REMOVED,
            subject_id=section.id,
            detail="A section present before the edit is absent after it.",
        )
        for section in before.sections
        if section.id not in after_ids
    ]


def _paragraph_changes(before: Document, after: Document) -> list[Change]:
    before_map = {paragraph.id: paragraph for paragraph in before.paragraphs()}
    after_map = {paragraph.id: paragraph for paragraph in after.paragraphs()}
    changes: list[Change] = []

    for paragraph_id, original in before_map.items():
        revised = after_map.get(paragraph_id)
        if revised is None:
            changes.append(Change(kind=ChangeKind.PARAGRAPH_REMOVED, subject_id=paragraph_id))
            continue
        if _paragraph_hash(original) != _paragraph_hash(revised):
            changes.append(
                Change(
                    kind=ChangeKind.PARAGRAPH_TEXT_CHANGED,
                    subject_id=paragraph_id,
                    detail=(f"{original.word_count()} words -> {revised.word_count()} words"),
                )
            )

    changes.extend(
        Change(kind=ChangeKind.PARAGRAPH_ADDED, subject_id=paragraph_id)
        for paragraph_id in after_map
        if paragraph_id not in before_map
    )
    return changes


def _paragraph_hash(paragraph: Paragraph) -> str:
    return text_sha256(paragraph.text)


def _citation_changes(before: Document, after: Document) -> list[Change]:
    before_home = _citation_homes(before)
    after_home = _citation_homes(after)
    changes: list[Change] = []

    for citation_id, node in before.citations.items():
        revised = after.citations.get(citation_id)

        if revised is None or citation_id not in after_home:
            changes.append(
                Change(
                    kind=ChangeKind.CITATION_REMOVED,
                    subject_id=citation_id,
                    related_ids=(before_home.get(citation_id, ""),),
                    detail=_describe(node),
                )
            )
            continue

        if node.signature() != revised.signature():
            changes.append(
                Change(
                    kind=ChangeKind.CITATION_ITEM_CHANGED,
                    subject_id=citation_id,
                    detail="The occurrence's items, mode, or modifiers changed.",
                )
            )

        origin = before_home.get(citation_id, "")
        destination = after_home.get(citation_id, "")
        if origin != destination:
            changes.append(
                Change(
                    kind=ChangeKind.CITATION_MOVED,
                    subject_id=citation_id,
                    related_ids=(origin, destination),
                )
            )

    changes.extend(
        Change(
            kind=ChangeKind.CITATION_ADDED,
            subject_id=citation_id,
            related_ids=(after_home.get(citation_id, ""),),
            detail=_describe(node),
        )
        for citation_id, node in after.citations.items()
        if citation_id not in before.citations
    )
    return changes


def _citation_homes(document: Document) -> dict[str, str]:
    homes: dict[str, str] = {}
    for paragraph in document.paragraphs():
        for citation_id in paragraph.citation_ids:
            homes[citation_id] = paragraph.id
    return homes


def _describe(node: CitationNode) -> str:
    return ", ".join(node.reference_ids) or "unlinked"


def _reference_changes(before: Document, after: Document) -> list[Change]:
    before_ids = {reference.id: reference for reference in before.references}
    after_ids = {reference.id: reference for reference in after.references}
    changes: list[Change] = []

    changes.extend(
        Change(
            kind=ChangeKind.REFERENCE_REMOVED,
            subject_id=reference_id,
            detail="A bibliography entry present before the edit is absent after it.",
        )
        for reference_id in before_ids
        if reference_id not in after_ids
    )
    changes.extend(
        Change(
            kind=ChangeKind.REFERENCE_ADDED,
            subject_id=reference_id,
            detail=(after_ids[reference_id].csl.title or "")[:120],
        )
        for reference_id in after_ids
        if reference_id not in before_ids
    )
    return changes


def _block_changes(before: Document, after: Document) -> list[Change]:
    changes: list[Change] = []
    for block_id, block in before.blocks.items():
        revised = after.blocks.get(block_id)
        if revised is None:
            changes.append(Change(kind=ChangeKind.BLOCK_REMOVED, subject_id=block_id))
        elif _block_hash(block) != _block_hash(revised):
            changes.append(Change(kind=ChangeKind.BLOCK_CHANGED, subject_id=block_id))
    return changes


def _block_hash(block: PreservedBlock) -> str:
    return canonical_sha256(block)
