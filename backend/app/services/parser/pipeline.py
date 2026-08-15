from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import DocumentRevision, Paper
from app.domain.block import BlockKind
from app.domain.citation import SemanticParseStatus
from app.domain.document import Document
from app.domain.errors import (
    AppError,
    InvalidStateTransitionError,
    NotFoundError,
    ParserFailedError,
    StaleRevisionError,
    as_app_error,
)
from app.domain.lifecycle import PaperStatus
from app.domain.reference import NormalizationStatus
from app.observability import get_logger
from app.providers.grobid import GrobidClient
from app.services.citations.style import detect_style
from app.services.deadline import Deadline
from app.services.parser.postvalidator import PostValidationReport, postvalidate
from app.services.parser.source_assets import attach_source_assets
from app.services.parser.tei_mapper import MappedDocument, map_tei
from app.services.parser.xml_safety import parse_tei
from app.settings import get_settings
from app.storage import manuscripts

logger = get_logger(__name__)


@dataclass(frozen=True)
class ParseOutcome:
    paper_id: str
    revision_id: str
    document: Document
    report: PostValidationReport


def content_digest(content: bytes) -> str:
    return manuscripts.sha256_bytes(content)


def validate_upload(content: bytes) -> None:
    manuscripts.validate_pdf(content)


def create_paper(session: Session, *, filename: str, content: bytes) -> Paper:
    manuscripts.validate_pdf(content)
    storage_id, directory = manuscripts.new_storage_dir()
    try:
        manuscripts.store_pdf(directory, content)
        paper = Paper(
            id=repositories.new_id("paper"),
            storage_id=storage_id,
            original_filename=filename[:512],
            content_sha256=manuscripts.sha256_bytes(content),
            size_bytes=len(content),
            status=PaperStatus.UPLOADED.value,
        )
        session.add(paper)
        session.flush()
    except Exception:
        manuscripts.discard_directory(directory)
        raise
    return paper


def parse_paper(
    session: Session,
    paper_id: str,
    *,
    operation_id: str,
    grobid: GrobidClient | None = None,
) -> ParseOutcome:
    settings = get_settings()
    deadline = Deadline.after("parse", settings.parse_deadline_seconds)

    directory = _claim_for_parsing(session, paper_id, operation_id)

    try:
        tei = _run_grobid(directory, deadline, grobid or GrobidClient())
        deadline.check("map")
        mapped = map_tei(parse_tei(tei))
        mapped = MappedDocument(
            document=attach_source_assets(
                mapped.document,
                directory / "original.pdf",
                directory,
            ),
            diagnostics=mapped.diagnostics,
        )
        validated = postvalidate(mapped)
        return _commit_parse(session, paper_id, operation_id, validated.document, validated.report)
    except Exception as error:
        _record_failure(session, paper_id, operation_id, as_app_error(error))
        raise


def _claim_for_parsing(session: Session, paper_id: str, operation_id: str) -> Path:
    repositories.lock_paper(session, paper_id)
    paper = repositories.get_paper(session, paper_id)

    if paper.status not in {
        PaperStatus.UPLOADED.value,
        PaperStatus.PARSE_FAILED.value,
        PaperStatus.PARSED.value,
    }:
        raise InvalidStateTransitionError(
            "This paper cannot be parsed in its current state.",
            paper_id=paper_id,
            status=paper.status,
        )

    paper.status = PaperStatus.PARSING.value
    paper.failure_code = None
    paper.failure_detail = None

    paper.active_parse_op_id = operation_id
    session.commit()

    return get_settings().papers_dir / paper.storage_id


def _run_grobid(directory: Path, deadline: Deadline, client: GrobidClient) -> str:
    deadline.check("grobid")
    pdf = directory / "original.pdf"
    if not pdf.exists():
        raise NotFoundError("The stored manuscript file is missing.")

    tei = client.process_fulltext(
        pdf.read_bytes(), timeout=deadline.budget_for(get_settings().grobid_timeout_seconds)
    )

    manuscripts.tei_path(directory).write_text(tei, encoding="utf-8")
    return tei


def _commit_parse(
    session: Session,
    paper_id: str,
    operation_id: str,
    document: Document,
    report: PostValidationReport,
) -> ParseOutcome:
    repositories.lock_paper(session, paper_id)
    paper = repositories.get_paper(session, paper_id)

    if paper.active_parse_op_id != operation_id:
        raise StaleRevisionError(
            "A newer parse superseded this one.",
            paper_id=paper_id,
            operation_id=operation_id,
        )

    if not document.sections:
        raise ParserFailedError(
            "No prose sections were recovered from this document.", paper_id=paper_id
        )

    revision = DocumentRevision(
        id=repositories.new_id("rev"),
        paper_id=paper_id,
        revision_number=repositories.next_revision_number(session, paper_id),
        document=document.model_dump(mode="json"),
        content_sha256=document.content_hash(),
        parent_revision_id=None,
        segmenter_version=document.segmenter_version,
        parse_report=report_summary(report),
    )
    session.add(revision)

    detection = detect_style(document)
    paper.status = PaperStatus.PARSED.value
    paper.active_parse_op_id = None
    paper.title = document.title or None
    paper.current_revision_id = revision.id

    paper.detected_citation_style = detection.style.value if detection.style else None
    paper.detected_style_confidence = detection.confidence.value
    session.commit()

    logger.info(
        "parse.completed",
        extra={
            "paper_id": paper_id,
            "revision_id": revision.id,
            "sections": len(document.sections),
            "citations": len(document.citations),
            "references": len(document.references),
            "linkage_accuracy": round(report.linkage_accuracy, 4),
        },
    )
    return ParseOutcome(
        paper_id=paper_id, revision_id=revision.id, document=document, report=report
    )


def _record_failure(session: Session, paper_id: str, operation_id: str, error: AppError) -> None:
    session.rollback()
    repositories.lock_paper(session, paper_id)
    paper = repositories.get_paper(session, paper_id)
    if paper.active_parse_op_id != operation_id:
        session.commit()
        return
    paper.status = PaperStatus.PARSE_FAILED.value
    paper.active_parse_op_id = None
    paper.failure_code = error.code.value

    paper.failure_detail = error.message
    session.commit()


def report_summary(report: PostValidationReport) -> dict[str, Any]:
    return {
        "family": report.family.value,
        "checked": report.checked,
        "agreed": report.agreed,
        "mismatched": report.mismatched,
        "recovered": report.recovered,
        "ambiguous": report.ambiguous,
        "unresolved": report.unresolved,
        "uncertain": report.uncertain,
        "not_a_marker": report.not_a_marker,
        "accuracy": round(report.linkage_accuracy, 4),
        "downgraded": len(report.downgraded_citation_ids),
        "promoted": len(report.promoted_citation_ids),
        "bibliography_expected": report.bibliography_expected,
        "bibliography_retained": report.bibliography_retained,
        "bibliography_complete": report.bibliography_complete,
    }


@dataclass(frozen=True)
class CitationCounts:
    total: int
    linked: int
    unlinked: int
    raw_only: int
    partial_modifiers: int
    clusters: int


def citation_counts(document: Document) -> CitationCounts:
    total = linked = 0
    raw_only = partial = 0
    for node in document.citations.values():
        total += len(node.items)
        linked += sum(1 for item in node.items if item.is_linked)
        if node.semantic_parse_status is SemanticParseStatus.RAW_ONLY:
            raw_only += 1
        elif node.semantic_parse_status is SemanticParseStatus.PARTIAL_MODIFIERS:
            partial += 1
    return CitationCounts(
        total=total,
        linked=linked,
        unlinked=total - linked,
        raw_only=raw_only,
        partial_modifiers=partial,
        clusters=len(document.citations),
    )


def reference_counts(document: Document) -> dict[NormalizationStatus, int]:
    counts = dict.fromkeys(NormalizationStatus, 0)
    for reference in document.references:
        counts[reference.normalization_status] += 1
    return counts


def block_counts(document: Document) -> dict[BlockKind, int]:
    counts = dict.fromkeys(BlockKind, 0)
    for block in document.blocks.values():
        counts[block.kind] += 1
    return counts
