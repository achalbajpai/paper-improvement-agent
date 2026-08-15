from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SupportVerdict(StrEnum):
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE = "UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE"

    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"

    SOURCE_IDENTITY_UNCERTAIN = "SOURCE_IDENTITY_UNCERTAIN"

    SOURCE_UNRESOLVED = "SOURCE_UNRESOLVED"


MODEL_SELECTABLE_VERDICTS = (
    SupportVerdict.SUPPORTED,
    SupportVerdict.PARTIALLY_SUPPORTED,
    SupportVerdict.CONTRADICTED,
    SupportVerdict.UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE,
)


class ClaimAnchor(BaseModel):
    model_config = ConfigDict(frozen=True)

    paragraph_id: str
    sentence_index: int
    sentence_id: str

    text_hash: str

    segmenter_version: str


class EvidenceAnchor(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_record_id: str
    span_id: str
    char_start: int
    char_end: int
    text_hash: str


class FindingKind(StrEnum):
    CITATION_SUPPORT = "CITATION_SUPPORT"

    UNCITED_CLAIM = "UNCITED_CLAIM"

    MISSING_WORK = "MISSING_WORK"

    UNRESOLVED_REFERENCE = "UNRESOLVED_REFERENCE"

    UNLINKED_CITATION = "UNLINKED_CITATION"


class ReviewFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: FindingKind
    claim_anchor: ClaimAnchor

    occurrence_id: str | None = None
    reference_id: str | None = None
    verdict: SupportVerdict | None = None
    evidence: tuple[EvidenceAnchor, ...] = ()

    reason: str = ""

    suggested_source_record_ids: tuple[str, ...] = ()
    """Snapshotted provider records offered as work this claim does not cite.

    Source record ids, never bibliography reference ids: a suggestion is by
    definition not yet in the manuscript, and pointing at a reference id would
    claim it was.
    """

    suggestion_rationales: tuple[str, ...] = ()
    """Positionally paired with the suggestions. Model-authored interpretation,
    shown as such -- the works themselves come from provider records."""

    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    """Which provider, model and prompt produced the model-authored part of this
    finding. A verdict nobody can trace back is one nobody can re-examine, and a
    fallback provider can answer a call the primary was configured for."""


class ProviderDegradation(BaseModel):
    model_config = ConfigDict(frozen=True)

    provider: str
    code: str
    detail: str = ""
