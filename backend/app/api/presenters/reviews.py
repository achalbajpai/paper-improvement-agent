"""Review runs and their findings, with every quoted string resolved server-side."""

from __future__ import annotations

from typing import Any

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.api.presenters.shared import _author_names, _error_code, _https_only, _reference_label
from app.api.schemas import (
    ClaimLocation,
    DegradationOut,
    EvidenceSpan,
    FindingOut,
    ReviewRunOut,
    SuggestedWork,
)
from app.db.models import DocumentRevision, ReviewFindingRow, ReviewRun, SourceRecord
from app.domain.document import Document
from app.domain.errors import ErrorCode
from app.domain.lifecycle import RunStatus
from app.domain.reference import CSLItem
from app.domain.review import FindingKind, SupportVerdict
from app.services.parser.segmenter import segment_paragraph

_SEVERITY = case(
    (ReviewFindingRow.verdict == SupportVerdict.CONTRADICTED.value, 0),
    (ReviewFindingRow.kind == FindingKind.CITATION_SUPPORT.value, 1),
    (ReviewFindingRow.kind == FindingKind.UNRESOLVED_REFERENCE.value, 2),
    (ReviewFindingRow.kind == FindingKind.UNLINKED_CITATION.value, 3),
    (ReviewFindingRow.kind == FindingKind.MISSING_WORK.value, 4),
    else_=5,
)


def review_run(session: Session, run: ReviewRun) -> ReviewRunOut:
    """Build a review response, resolving every quoted string server-side.

    Findings store anchors, not text. The claim sentence is re-read from the
    revision and the evidence from the snapshotted abstract, so what a researcher
    sees quoted is what those artefacts contain -- and a finding whose anchor no
    longer resolves shows as missing rather than as stale text nobody noticed.
    """
    revision = session.get(DocumentRevision, run.revision_id)
    document = Document.model_validate(revision.document) if revision else None
    rows = (
        session.execute(
            select(ReviewFindingRow)
            .where(ReviewFindingRow.run_id == run.id)
            .order_by(_SEVERITY, ReviewFindingRow.id)
        )
        .scalars()
        .all()
    )
    sources = {
        record.id: record
        for record in session.execute(
            select(SourceRecord).where(SourceRecord.paper_id == run.paper_id)
        )
        .scalars()
        .all()
    }

    return ReviewRunOut(
        id=run.id,
        paper_id=run.paper_id,
        revision_id=run.revision_id,
        status=RunStatus(run.status),
        failure_code=_error_code(run.failure_code),
        degradations=[
            DegradationOut(
                provider=str(item.get("provider", "")),
                code=ErrorCode(item.get("code", ErrorCode.PROVIDER_UNAVAILABLE.value)),
                detail=str(item.get("detail", "")),
            )
            for item in run.degradations
        ],
        stats=dict(run.stats),
        findings=[finding(row, document, sources) for row in rows],
    )


def finding(
    row: ReviewFindingRow, document: Document | None, sources: dict[str, SourceRecord]
) -> FindingOut:
    anchor = dict(row.claim_anchor)
    paragraph_id = str(anchor.get("paragraph_id", ""))
    section = document.section_of(paragraph_id) if document and paragraph_id else None
    reference = document.reference(row.reference_id) if document and row.reference_id else None

    return FindingOut(
        id=row.id,
        kind=FindingKind(row.kind),
        verdict=SupportVerdict(row.verdict) if row.verdict else None,
        claim=ClaimLocation(
            paragraph_id=paragraph_id,
            sentence_id=str(anchor.get("sentence_id", "")),
            sentence_index=int(anchor.get("sentence_index", -1)),
            section_id=section.id if section else None,
            section_title=section.title if section else None,
            text=_claim_text(document, paragraph_id, str(anchor.get("sentence_id", ""))),
        ),
        occurrence_id=row.occurrence_id,
        reference_id=row.reference_id,
        reference_label=_reference_label(reference),
        evidence=[_evidence(dict(item), sources) for item in row.evidence],
        reason=row.reason,
        suggestions=_suggestions(row, sources),
        model_provider=row.provider,
        model=row.model,
        prompt_version=row.prompt_version,
        handled=row.handled_at is not None,
    )


def _suggestions(row: ReviewFindingRow, sources: dict[str, SourceRecord]) -> list[SuggestedWork]:
    """Resolve suggested work from the snapshots, dropping anything unlinkable.

    A suggestion whose snapshot is gone, or which has no https link and no DOI,
    is not rendered at all. "Cite this, I cannot tell you where to find it" is
    not a recommendation a researcher can act on.
    """
    rationales = [str(value) for value in row.suggestion_rationales]
    suggestions: list[SuggestedWork] = []

    for index, value in enumerate(row.suggested_source_record_ids):
        record = sources.get(str(value))
        if record is None:
            continue
        link = _https_only(record.url) or (f"https://doi.org/{record.doi}" if record.doi else None)
        if link is None:
            continue

        csl = CSLItem.model_validate(record.csl) if record.csl else None
        suggestions.append(
            SuggestedWork(
                source_record_id=record.id,
                title=record.title or "Untitled",
                authors=_author_names(csl),
                year=csl.year if csl else None,
                venue=csl.container_title if csl else None,
                doi=record.doi,
                url=link,
                provider=record.provider,
                rationale=rationales[index] if index < len(rationales) else "",
            )
        )
    return suggestions


def _claim_text(document: Document | None, paragraph_id: str, sentence_id: str) -> str:
    if document is None or not paragraph_id:
        return ""
    paragraph = document.paragraph(paragraph_id)
    if paragraph is None:
        return ""
    for sentence in segment_paragraph(paragraph):
        if sentence.id == sentence_id:
            return sentence.plain_text
    return ""


def _evidence(anchor: dict[str, Any], sources: dict[str, SourceRecord]) -> EvidenceSpan:
    record = sources.get(str(anchor.get("source_record_id", "")))
    start = int(anchor.get("char_start", 0))
    end = int(anchor.get("char_end", 0))
    abstract = record.abstract if record else None
    return EvidenceSpan(
        source_record_id=str(anchor.get("source_record_id", "")),
        span_id=str(anchor.get("span_id", "")),
        text=abstract[start:end].strip() if abstract else "",
        source_title=record.title if record else None,
        source_url=_https_only(record.url) if record else None,
    )
