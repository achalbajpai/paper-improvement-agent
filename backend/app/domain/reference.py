from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.errors import IdentityInvariantError
from app.domain.ids import Origin, origin_of

RAW_TEXT_KEY = "raw_text"


class NormalizationStatus(StrEnum):
    COMPLETE = "COMPLETE"

    PARTIAL = "PARTIAL"

    RAW_ONLY = "RAW_ONLY"


class ResolutionMethod(StrEnum):
    DOI = "DOI"
    ARXIV = "ARXIV"
    PUBMED = "PUBMED"
    EXTERNAL_ID = "EXTERNAL_ID"
    TITLE_AUTHOR_YEAR = "TITLE_AUTHOR_YEAR"
    TITLE_ONLY = "TITLE_ONLY"
    UNRESOLVED = "UNRESOLVED"


class ResolutionConfidence(StrEnum):
    CERTAIN = "CERTAIN"

    PROBABLE = "PROBABLE"

    UNCERTAIN = "UNCERTAIN"
    NONE = "NONE"


class CSLItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    type: str = "document"
    title: str | None = None
    author: list[dict[str, Any]] = Field(default_factory=list)
    issued: dict[str, Any] | None = None
    container_title: str | None = Field(default=None, alias="container-title")
    DOI: str | None = None
    URL: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)

    @property
    def year(self) -> int | None:
        parts = (self.issued or {}).get("date-parts") or []
        if parts and parts[0]:
            first = parts[0][0]
            return int(first) if isinstance(first, int | str) and str(first).isdigit() else None
        return None

    @property
    def raw_text(self) -> str:
        value = self.custom.get(RAW_TEXT_KEY, "")
        return str(value)

    def is_complete_for_insertion(self) -> bool:
        return bool(self.title) and bool(self.author) and self.year is not None


class ReferenceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str

    csl: CSLItem

    raw_text: str = ""

    bibliography_order: int = 0
    normalization_status: NormalizationStatus = NormalizationStatus.COMPLETE
    resolution_method: ResolutionMethod = ResolutionMethod.UNRESOLVED
    resolution_confidence: ResolutionConfidence = ResolutionConfidence.NONE

    source_record_id: str | None = None

    @model_validator(mode="after")
    def _csl_id_matches(self) -> ReferenceRecord:
        if self.csl.id != self.id:
            raise IdentityInvariantError(
                "A reference's CSL id must equal its record id.",
                reference_id=self.id,
                csl_id=self.csl.id,
            )
        return self

    @property
    def origin(self) -> Origin | None:
        return origin_of(self.id)

    @property
    def is_resolved(self) -> bool:
        return self.resolution_confidence in (
            ResolutionConfidence.CERTAIN,
            ResolutionConfidence.PROBABLE,
        )


def csl_for_unparseable(reference_id: str, raw_text: str) -> CSLItem:
    return CSLItem(id=reference_id, type="document", custom={RAW_TEXT_KEY: raw_text})
