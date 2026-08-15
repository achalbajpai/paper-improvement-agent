from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentRevision,
    EditProposal,
    ExportRun,
    OperationRequest,
    Paper,
    ReviewRun,
)
from app.domain.errors import (
    IdempotencyKeyReusedError,
    NotFoundError,
    OperationInProgressError,
)
from app.domain.hashing import canonical_sha256

LOCK_CLASS_PAPER = 1
LOCK_CLASS_PROPOSAL = 2


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def lock_paper(session: Session, paper_id: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:cls, hashtext(:key))"),
        {"cls": LOCK_CLASS_PAPER, "key": paper_id},
    )


def lock_proposal(session: Session, proposal_id: str) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:cls, hashtext(:key))"),
        {"cls": LOCK_CLASS_PROPOSAL, "key": proposal_id},
    )


@dataclass(frozen=True)
class ReplayedResponse:
    status_code: int
    body: dict[str, Any]


def begin_operation(
    session: Session,
    *,
    scope_type: str,
    scope_id: str | None,
    operation_type: str,
    idempotency_key: str,
    request_body: Any,
) -> OperationRequest | ReplayedResponse:
    request_sha = canonical_sha256(request_body)

    existing = session.execute(
        select(OperationRequest).where(
            OperationRequest.scope_type == scope_type,
            OperationRequest.scope_id.is_(scope_id)
            if scope_id is None
            else OperationRequest.scope_id == scope_id,
            OperationRequest.operation_type == operation_type,
            OperationRequest.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()

    if existing is not None:
        return _resolve_existing(existing, request_sha)

    claim = OperationRequest(
        id=new_id("op"),
        scope_type=scope_type,
        scope_id=scope_id,
        operation_type=operation_type,
        idempotency_key=idempotency_key,
        request_sha256=request_sha,
        status="IN_PROGRESS",
    )
    session.add(claim)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raced = session.execute(
            select(OperationRequest).where(
                OperationRequest.scope_type == scope_type,
                OperationRequest.scope_id.is_(scope_id)
                if scope_id is None
                else OperationRequest.scope_id == scope_id,
                OperationRequest.operation_type == operation_type,
                OperationRequest.idempotency_key == idempotency_key,
            )
        ).scalar_one()
        return _resolve_existing(raced, request_sha)
    return claim


def _resolve_existing(row: OperationRequest, request_sha: str) -> ReplayedResponse:
    if row.request_sha256 != request_sha:
        raise IdempotencyKeyReusedError(
            "This Idempotency-Key was already used for a different request.",
            operation_type=row.operation_type,
        )
    if row.status == "COMPLETED" and row.response_body is not None:
        return ReplayedResponse(
            status_code=row.response_status or 200, body=dict(row.response_body)
        )
    raise OperationInProgressError(
        "An identical request is still running.", operation_type=row.operation_type
    )


def complete_operation(
    session: Session, claim: OperationRequest, status_code: int, body: dict[str, Any]
) -> None:
    claim.status = "COMPLETED"
    claim.response_status = status_code
    claim.response_body = body
    session.flush()


def fail_operation(session: Session, claim: OperationRequest) -> None:
    session.delete(claim)
    session.flush()


def get_paper(session: Session, paper_id: str) -> Paper:
    paper = session.get(Paper, paper_id)
    if paper is None:
        raise NotFoundError("No such paper.", paper_id=paper_id)
    return paper


def get_revision(session: Session, revision_id: str) -> DocumentRevision:
    revision = session.get(DocumentRevision, revision_id)
    if revision is None:
        raise NotFoundError("No such revision.", revision_id=revision_id)
    return revision


def get_current_revision(session: Session, paper: Paper) -> DocumentRevision:
    if not paper.current_revision_id:
        raise NotFoundError("This paper has not been parsed yet.", paper_id=paper.id)
    return get_revision(session, paper.current_revision_id)


def get_proposal(session: Session, proposal_id: str) -> EditProposal:
    proposal = session.get(EditProposal, proposal_id)
    if proposal is None:
        raise NotFoundError("No such proposal.", proposal_id=proposal_id)
    return proposal


def get_review_run(session: Session, run_id: str) -> ReviewRun:
    run = session.get(ReviewRun, run_id)
    if run is None:
        raise NotFoundError("No such review run.", run_id=run_id)
    return run


def get_export_run(session: Session, run_id: str) -> ExportRun:
    run = session.get(ExportRun, run_id)
    if run is None:
        raise NotFoundError("No such export run.", run_id=run_id)
    return run


def next_revision_number(session: Session, paper_id: str) -> int:
    numbers = session.execute(
        select(DocumentRevision.revision_number).where(DocumentRevision.paper_id == paper_id)
    ).scalars()
    return max(numbers, default=0) + 1


def list_papers(session: Session, limit: int = 50) -> Sequence[Paper]:
    return (
        session.execute(select(Paper).order_by(Paper.created_at.desc()).limit(limit))
        .scalars()
        .all()
    )


def list_review_runs(session: Session, paper_id: str, limit: int = 20) -> Sequence[ReviewRun]:
    return (
        session.execute(
            select(ReviewRun)
            .where(ReviewRun.paper_id == paper_id)
            .order_by(ReviewRun.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def list_proposals(session: Session, paper_id: str, limit: int = 20) -> Sequence[EditProposal]:
    return (
        session.execute(
            select(EditProposal)
            .where(EditProposal.paper_id == paper_id)
            .order_by(EditProposal.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )


def active_proposals(session: Session, paper_id: str) -> Sequence[EditProposal]:
    return (
        session.execute(
            select(EditProposal).where(
                EditProposal.paper_id == paper_id,
                EditProposal.state.in_(("PENDING", "AWAITING_DECISION")),
            )
        )
        .scalars()
        .all()
    )


def unsettled_proposals(session: Session, paper_id: str) -> Sequence[EditProposal]:
    return (
        session.execute(
            select(EditProposal).where(
                EditProposal.paper_id == paper_id,
                EditProposal.state.in_(("PENDING", "AWAITING_DECISION", "BLOCKED")),
            )
        )
        .scalars()
        .all()
    )
