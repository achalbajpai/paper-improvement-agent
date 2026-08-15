"""What actually changed between two snapshots.

The DeltaEngine that produces this compares the before and after ASTs directly.
It never reads the model's account of what it did, and it never reads the
editing code's account either.

The reason is the failure mode this system is built around: a model asked to
shorten a paragraph reports "removed two sentences" while having also dropped a
citation. Both the report and the mutation come from the same untrusted step, so
one cannot check the other. Only an independent comparison of the two documents
can, and this is its output.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ChangeKind(StrEnum):
    PARAGRAPH_TEXT_CHANGED = "PARAGRAPH_TEXT_CHANGED"
    PARAGRAPH_ADDED = "PARAGRAPH_ADDED"
    PARAGRAPH_REMOVED = "PARAGRAPH_REMOVED"

    CITATION_ADDED = "CITATION_ADDED"
    CITATION_REMOVED = "CITATION_REMOVED"

    CITATION_MOVED = "CITATION_MOVED"

    CITATION_ITEM_CHANGED = "CITATION_ITEM_CHANGED"

    REFERENCE_ADDED = "REFERENCE_ADDED"
    REFERENCE_REMOVED = "REFERENCE_REMOVED"

    BLOCK_REMOVED = "BLOCK_REMOVED"
    BLOCK_CHANGED = "BLOCK_CHANGED"

    SECTION_REMOVED = "SECTION_REMOVED"


class Change(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ChangeKind

    subject_id: str

    related_ids: tuple[str, ...] = ()
    detail: str = ""


class ComputedEditDelta(BaseModel):
    """The authoritative account of one edit.

    Word counts come in two pairs because they answer two different questions.
    The ``words_*`` pair is the whole manuscript, which is what a researcher
    needs to know about the paper. The ``scope_words_*`` pair counts only the
    paragraphs this edit actually changed, which is what they need to know about
    the edit.

    Reporting the document pair as though it were the edit's own size is how a
    126-word trim to an introduction gets displayed as ``6962 -> 6836`` and reads
    as though the whole paper had been rewritten. The difference is identical
    either way; the denominator is not.
    """

    model_config = ConfigDict(frozen=True)

    changes: tuple[Change, ...] = Field(default_factory=tuple)
    words_before: int = 0
    words_after: int = 0

    scope_words_before: int = 0
    scope_words_after: int = 0

    def of_kind(self, *kinds: ChangeKind) -> tuple[Change, ...]:
        return tuple(change for change in self.changes if change.kind in kinds)

    @property
    def changed_paragraph_ids(self) -> tuple[str, ...]:
        return tuple(
            change.subject_id
            for change in self.of_kind(
                ChangeKind.PARAGRAPH_TEXT_CHANGED,
                ChangeKind.PARAGRAPH_ADDED,
                ChangeKind.PARAGRAPH_REMOVED,
            )
        )

    @property
    def removed_citation_ids(self) -> tuple[str, ...]:
        return tuple(change.subject_id for change in self.of_kind(ChangeKind.CITATION_REMOVED))

    @property
    def added_citation_ids(self) -> tuple[str, ...]:
        return tuple(change.subject_id for change in self.of_kind(ChangeKind.CITATION_ADDED))

    @property
    def added_reference_ids(self) -> tuple[str, ...]:
        return tuple(change.subject_id for change in self.of_kind(ChangeKind.REFERENCE_ADDED))

    @property
    def removed_reference_ids(self) -> tuple[str, ...]:
        return tuple(change.subject_id for change in self.of_kind(ChangeKind.REFERENCE_REMOVED))

    @property
    def touches_blocks(self) -> bool:
        return bool(self.of_kind(ChangeKind.BLOCK_REMOVED, ChangeKind.BLOCK_CHANGED))

    @property
    def is_empty(self) -> bool:
        return not self.changes

    def summary(self) -> str:
        """A plain-language account, built from the computed changes only.

        This is what the explanation prompt is shown. It contains identifiers and
        counts, never manuscript prose.
        """
        if self.is_empty:
            return "No changes."
        counts: dict[str, int] = {}
        for change in self.changes:
            counts[change.kind.value] = counts.get(change.kind.value, 0) + 1
        parts = [f"{value} {key}" for key, value in sorted(counts.items())]
        if not self.changed_paragraph_ids:
            return f"{'; '.join(parts)}. No prose changed."
        return (
            f"{'; '.join(parts)}. "
            f"{self.scope_words_before} -> {self.scope_words_after} words "
            f"in the {len(set(self.changed_paragraph_ids))} paragraphs it changed."
        )
