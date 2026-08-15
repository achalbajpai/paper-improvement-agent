from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.document import Document
from app.domain.reference import ReferenceRecord


@dataclass(frozen=True)
class RenderSet:
    references: tuple[ReferenceRecord, ...]

    retained_uncited_ids: tuple[str, ...]

    def csl_json(self) -> list[dict[str, Any]]:
        return [
            reference.csl.model_dump(mode="json", by_alias=True, exclude_none=True)
            for reference in self.references
        ]


def build(document: Document, *, original_reference_ids: frozenset[str]) -> RenderSet:
    cited = document.cited_reference_ids()
    retained = tuple(
        reference.id
        for reference in document.references
        if reference.id not in cited and reference.id in original_reference_ids
    )
    return RenderSet(references=document.references, retained_uncited_ids=retained)


def dropped_added_reference_ids(
    document: Document, *, original_reference_ids: frozenset[str]
) -> tuple[str, ...]:
    cited = document.cited_reference_ids()
    return tuple(
        reference.id
        for reference in document.references
        if reference.id not in cited and reference.id not in original_reference_ids
    )
