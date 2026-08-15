from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.presenters.shared import _error_code
from app.api.schemas import (
    PaperDetail,
    PaperSummary,
    ParseQuality,
    ParseQualityBlocks,
    ParseQualityCitations,
    ParseQualityLinkage,
    ParseQualityReferences,
)
from app.db.models import DocumentRevision, Paper
from app.domain.document import Document
from app.domain.lifecycle import CitationStyle, PaperStatus, StyleConfidence
from app.domain.reference import NormalizationStatus
from app.services.citations.style import detect_style
from app.services.parser import pipeline


def paper_summary(paper: Paper) -> PaperSummary:
    return PaperSummary(
        id=paper.id,
        original_filename=paper.original_filename,
        title=paper.title,
        status=PaperStatus(paper.status),
        failure_code=_error_code(paper.failure_code),
        citation_style=CitationStyle(paper.citation_style) if paper.citation_style else None,
        detected_citation_style=(
            CitationStyle(paper.detected_citation_style) if paper.detected_citation_style else None
        ),
        detected_style_confidence=(
            StyleConfidence(paper.detected_style_confidence)
            if paper.detected_style_confidence
            else None
        ),
        current_revision_id=paper.current_revision_id,
        created_at=paper.created_at,
    )


def paper_detail(session: Session, paper: Paper) -> PaperDetail:
    summary = paper_summary(paper)
    revision_count = (
        session.execute(
            select(func.count())
            .select_from(DocumentRevision)
            .where(DocumentRevision.paper_id == paper.id)
        ).scalar_one()
        or 0
    )

    quality: ParseQuality | None = None
    reason: str | None = None
    if paper.current_revision_id:
        revision = session.get(DocumentRevision, paper.current_revision_id)
        if revision is not None:
            document = Document.model_validate(revision.document)
            quality = parse_quality(document, _first_report(session, paper.id))
            reason = detect_style(document).reason

    return PaperDetail(
        **summary.model_dump(),
        parse_quality=quality,
        detected_style_reason=reason,
        revision_count=revision_count,
    )


def _first_report(session: Session, paper_id: str) -> dict[str, Any] | None:
    row = session.execute(
        select(DocumentRevision.parse_report).where(
            DocumentRevision.paper_id == paper_id, DocumentRevision.revision_number == 1
        )
    ).scalar_one_or_none()
    return dict(row) if row else None


def parse_quality(document: Document, report: dict[str, Any] | None) -> ParseQuality:
    citations = pipeline.citation_counts(document)
    references = pipeline.reference_counts(document)
    blocks = pipeline.block_counts(document)

    linkage = None
    retained = True
    if report:
        linkage = ParseQualityLinkage(
            family=str(report.get("family", "UNKNOWN")),
            checked=int(report.get("checked", 0)),
            agreed=int(report.get("agreed", 0)),
            mismatched=int(report.get("mismatched", 0)),
            recovered=int(report.get("recovered", 0)),
            ambiguous=int(report.get("ambiguous", 0)),
            unresolved=int(report.get("unresolved", 0)),
            uncertain=int(report.get("uncertain", 0)),
            accuracy=float(report.get("accuracy", 0.0)),
            downgraded=int(report.get("downgraded", 0)),
            promoted=int(report.get("promoted", 0)),
        )
        retained = bool(report.get("bibliography_complete", True))

    return ParseQuality(
        sections=len(document.sections),
        paragraphs=len(document.paragraphs()),
        words=document.word_count(),
        citations=ParseQualityCitations(
            total=citations.total,
            linked=citations.linked,
            unlinked=citations.unlinked,
            raw_only=citations.raw_only,
            partial_modifiers=citations.partial_modifiers,
            clusters=citations.clusters,
        ),
        references=ParseQualityReferences(
            total=len(document.references),
            complete=references[NormalizationStatus.COMPLETE],
            partial=references[NormalizationStatus.PARTIAL],
            raw_only=references[NormalizationStatus.RAW_ONLY],
        ),
        blocks=ParseQualityBlocks(
            total=len(document.blocks),
            by_kind={kind.value: count for kind, count in blocks.items() if count},
            unrenderable=len(document.unrenderable_block_ids()),
            unrenderable_ids=list(document.unrenderable_block_ids()),
            floating=len(document.floating_block_ids),
        ),
        linkage=linkage,
        bibliography_retained=retained,
    )
