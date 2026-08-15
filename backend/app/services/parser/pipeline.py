"""Upload and parse, as two operations.

They are split because they fail for unrelated reasons and on unrelated
timescales. An upload is a fast, local write that either happened or did not. A
parse is a minute of GROBID plus mapping, and it can be superseded, retried, or
time out. Fusing them would mean a GROBID hiccup discarded a file the researcher
already handed over.

The parse runs in **three phases**, and the shape matters:

1. A short transaction claims the paper, moves it to PARSING, and records *which*
   parse owns it.
2. The slow work -- GROBID, mapping, postvalidation -- runs with no transaction
   open. Holding one across a ninety-second HTTP call would pin a connection and
   a row lock for the entire time.
3. A second short transaction re-takes the lock, **checks ownership before
   writing anything**, and only then creates the revision.

Phase 3's ownership check is the point. Without it, a parse that was superseded
while it was running would still write a revision, and the paper would end up
pointing at the older of two results with the newer one orphaned behind it.
"""

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
    """The identity of an upload, for idempotency. Same bytes, same request."""
    return manuscripts.sha256_bytes(content)


def validate_upload(content: bytes) -> None:
    """Reject invalid bytes before an idempotency claim or filesystem write."""
    manuscripts.validate_pdf(content)


def create_paper(session: Session, *, filename: str, content: bytes) -> Paper:
    """Validate and store one PDF, then record it.

    The file is written before the row exists, so a crash between the two leaves
    an unreferenced directory rather than a row pointing at nothing. The
    directory is removed on failure; a reconciliation sweep handles the crash
    case.
    """
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
    """Phase 1. Take ownership of the paper's parse slot."""
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
    """Phase 2. The slow part, with no transaction open."""
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
    """Phase 3. Ownership first, then write."""
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
    """Persist a typed failure, but only if this parse still owns the paper.

    Reached from every exit that is not a successful commit, including the
    commit itself and anything unanticipated. A paper left in ``PARSING`` cannot
    be parsed again -- ``_claim_for_parsing`` refuses that state -- so a failure
    that skipped this would take the paper out of the product permanently.
    """
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
    """The stored form of a postvalidation report.

    Counts and identifiers only. The per-item issue list carries marker text,
    which is manuscript prose, so it stays in memory for the evaluation harness
    and never reaches a persisted row that a log or an export might surface.
    """
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
    """Counted per *item*, not per occurrence, except where noted.

    ``[2, 5]`` asserts two citations. Reporting it as one would understate both
    the work done and the risk carried.
    """
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
