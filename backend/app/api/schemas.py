from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.citation import SemanticParseStatus
from app.domain.errors import ErrorCode
from app.domain.lifecycle import (
    CitationStyle,
    PaperStatus,
    RunStatus,
    StyleConfidence,
)
from app.domain.proposal import ProposalState
from app.domain.reference import (
    NormalizationStatus,
    ResolutionConfidence,
    ResolutionMethod,
)
from app.domain.review import FindingKind, SupportVerdict
from app.domain.verification import BlockerCode, CheckName, CheckStatus, WarningCode

__all__ = [
    "CitationStyle",
    "PaperStatus",
    "RunStatus",
    "StyleConfidence",
]


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(BaseModel):
    error: ErrorDetail


class PaperSummary(BaseModel):
    id: str
    original_filename: str
    title: str | None
    status: PaperStatus
    failure_code: ErrorCode | None = None
    citation_style: CitationStyle | None = None
    detected_citation_style: CitationStyle | None = None
    detected_style_confidence: StyleConfidence | None = None
    current_revision_id: str | None = None
    created_at: datetime


class ParseQualityCitations(BaseModel):
    total: int
    linked: int
    unlinked: int
    raw_only: int
    partial_modifiers: int
    clusters: int


class ParseQualityReferences(BaseModel):
    total: int
    complete: int
    partial: int
    raw_only: int


class ParseQualityBlocks(BaseModel):
    total: int
    by_kind: dict[str, int]
    unrenderable: int
    unrenderable_ids: list[str]

    floating: int


class ParseQualityLinkage(BaseModel):
    family: str
    checked: int
    agreed: int
    mismatched: int
    recovered: int
    ambiguous: int
    unresolved: int
    uncertain: int
    accuracy: float
    downgraded: int
    promoted: int


class ParseQuality(BaseModel):
    sections: int
    paragraphs: int
    words: int
    citations: ParseQualityCitations
    references: ParseQualityReferences
    blocks: ParseQualityBlocks
    linkage: ParseQualityLinkage | None = None
    bibliography_retained: bool = True


class PaperDetail(PaperSummary):
    parse_quality: ParseQuality | None = None
    detected_style_reason: str | None = None
    revision_count: int = 0


class SetCitationStyleRequest(BaseModel):
    citation_style: CitationStyle


class InlineCitation(BaseModel):
    citation_id: str
    raw_marker: str
    reference_ids: list[str] = Field(default_factory=list)
    parse_status: SemanticParseStatus
    is_unlinked: bool = False
    fidelity_exportable: bool = True


class InlineRun(BaseModel):
    kind: Literal["text", "citation"]
    text: str = ""
    citation: InlineCitation | None = None


class ParagraphOut(BaseModel):
    id: str
    order_index: int
    inlines: list[InlineRun] = Field(default_factory=list)
    word_count: int = 0


class SectionOut(BaseModel):
    id: str
    title: str
    level: int = 1
    order_index: int = 0
    paragraphs: list[ParagraphOut] = Field(default_factory=list)


class ReferenceOut(BaseModel):
    id: str
    bibliography_order: int
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    container_title: str | None = None
    doi: str | None = None
    url: str | None = None
    raw_text: str = ""
    normalization_status: NormalizationStatus
    resolution_method: ResolutionMethod
    resolution_confidence: ResolutionConfidence
    source_record_id: str | None = None
    occurrences: int = 0


class ManuscriptOut(BaseModel):
    paper_id: str
    revision_id: str
    revision_number: int
    title: str = ""
    abstract: str = ""
    sections: list[SectionOut] = Field(default_factory=list)
    references: list[ReferenceOut] = Field(default_factory=list)
    unlinked_citation_ids: list[str] = Field(default_factory=list)
    raw_only_citation_ids: list[str] = Field(default_factory=list)


class EvidenceSpan(BaseModel):
    source_record_id: str
    span_id: str

    text: str
    source_title: str | None = None
    source_url: str | None = None


class ClaimLocation(BaseModel):
    paragraph_id: str
    sentence_id: str
    sentence_index: int
    section_id: str | None = None
    section_title: str | None = None

    text: str


class SuggestedWork(BaseModel):
    source_record_id: str
    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None
    provider: str
    rationale: str = ""


class FindingOut(BaseModel):
    id: str
    kind: FindingKind
    verdict: SupportVerdict | None = None
    claim: ClaimLocation
    occurrence_id: str | None = None
    reference_id: str | None = None
    reference_label: str | None = None
    evidence: list[EvidenceSpan] = Field(default_factory=list)
    reason: str = ""
    suggestions: list[SuggestedWork] = Field(default_factory=list)
    model_provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None

    handled: bool = False


class HandleFindingRequest(BaseModel):
    handled: bool


class DegradationOut(BaseModel):
    provider: str
    code: ErrorCode
    detail: str = ""


class ReviewRunOut(BaseModel):
    id: str
    paper_id: str
    revision_id: str
    status: RunStatus
    failure_code: ErrorCode | None = None

    degradations: list[DegradationOut] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    findings: list[FindingOut] = Field(default_factory=list)


class CreateProposalRequest(BaseModel):
    command: str = Field(min_length=1, max_length=1000)

    section_id: str | None = Field(
        default=None,
        description="Bind the command to this section instead of inferring one from the prose.",
    )
    paragraph_id: str | None = Field(
        default=None,
        description="Bind the command to this paragraph. Narrows the edit to it alone.",
    )


class ChangeOut(BaseModel):
    kind: str
    subject_id: str
    related_ids: list[str] = Field(default_factory=list)
    detail: str = ""


class DeltaOut(BaseModel):
    changes: list[ChangeOut]
    words_before: int
    words_after: int

    scope_words_before: int = 0
    scope_words_after: int = 0

    summary: str


class SkippedParagraphOut(BaseModel):
    paragraph_id: str
    reason: str


class EditScopeOut(BaseModel):
    section_id: str
    section_title: str = ""
    paragraph_id: str | None = None
    targeted_paragraph_ids: list[str] = Field(default_factory=list)
    skipped: list[SkippedParagraphOut] = Field(default_factory=list)
    words_before: int = 0
    words_target: int = 0
    section_paragraph_count: int = 0


class WarningOut(BaseModel):
    id: str
    code: WarningCode

    message: str
    subject_ids: list[str] = Field(default_factory=list)


class BlockerOut(BaseModel):
    code: BlockerCode
    message: str
    subject_ids: list[str] = Field(default_factory=list)


class CheckOut(BaseModel):
    name: CheckName
    status: CheckStatus
    detail: str = ""
    prompt_version: str | None = None
    model: str | None = None
    model_provider: str | None = None


class ParagraphDiff(BaseModel):
    paragraph_id: str
    section_id: str | None = None
    before_text: str
    after_text: str
    before_citation_ids: list[str] = Field(default_factory=list)
    after_citation_ids: list[str] = Field(default_factory=list)


class ProposalOut(BaseModel):
    id: str
    paper_id: str
    base_revision_id: str
    command: str
    intent: str | None = None
    state: ProposalState
    failure_code: ErrorCode | None = None
    failure_detail: str | None = None
    candidate_sha256: str | None = None
    delta: DeltaOut | None = None
    checks: list[CheckOut] = Field(default_factory=list)
    blockers: list[BlockerOut] = Field(default_factory=list)
    warnings: list[WarningOut] = Field(default_factory=list)
    required_warning_ids: list[str] = Field(default_factory=list)

    explanation: str = ""
    scope: EditScopeOut | None = None
    diffs: list[ParagraphDiff] = Field(default_factory=list)


class AcceptProposalRequest(BaseModel):
    acknowledged_warning_ids: list[str] = Field(default_factory=list)

    candidate_sha256: str


class AcceptProposalResponse(BaseModel):
    proposal_id: str
    revision_id: str
    revision_number: int


class PreflightItem(BaseModel):
    code: str
    message: str
    subject_ids: list[str] = Field(default_factory=list)


class ExportPreflight(BaseModel):
    revision_id: str
    citation_style: CitationStyle | None
    can_export: bool
    blockers: list[PreflightItem] = Field(default_factory=list)
    warnings: list[WarningOut] = Field(default_factory=list)


class CreateExportRequest(BaseModel):
    acknowledged_warning_ids: list[str] = Field(default_factory=list)


class ArtifactOut(BaseModel):
    name: str
    media_type: str
    size_bytes: int

    href: str


class ExportRunOut(BaseModel):
    id: str
    paper_id: str
    revision_id: str
    citation_style: CitationStyle
    status: RunStatus
    failure_code: ErrorCode | None = None
    failure_detail: str | None = None
    artifacts: list[ArtifactOut] = Field(default_factory=list)
