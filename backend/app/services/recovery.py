from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EditProposal, ExportRun, OperationRequest, Paper, ReviewRun
from app.domain.errors import ErrorCode
from app.domain.lifecycle import PaperStatus, RunStatus
from app.domain.proposal import ProposalState
from app.observability import get_logger

logger = get_logger(__name__)

FAILURE_CODE = ErrorCode.INTERNAL_ERROR.value
FAILURE_DETAIL = "This operation was interrupted before it finished and did not resume."


def reconcile_interrupted(
    session: Session, *, older_than: timedelta = timedelta(0)
) -> dict[str, int]:
    cutoff = datetime.now(UTC) - older_than
    counts = {
        "papers": _fail_papers(session, cutoff),
        "proposals": _fail_proposals(session, cutoff),
        "reviews": _fail_runs(_pending(session, ReviewRun, cutoff)),
        "exports": _fail_runs(_pending(session, ExportRun, cutoff)),
        "claims": _release_claims(session, cutoff),
    }
    session.commit()

    if any(counts.values()):
        logger.warning("recovery.interrupted_operations_failed", extra=counts)
    return counts


def _fail_papers(session: Session, cutoff: datetime) -> int:
    papers = (
        session.execute(
            select(Paper).where(
                Paper.status == PaperStatus.PARSING.value, Paper.updated_at <= cutoff
            )
        )
        .scalars()
        .all()
    )
    for paper in papers:
        paper.status = PaperStatus.PARSE_FAILED.value
        paper.failure_code = FAILURE_CODE
        paper.failure_detail = FAILURE_DETAIL
        paper.active_parse_op_id = None
    return len(papers)


def _fail_proposals(session: Session, cutoff: datetime) -> int:
    proposals = (
        session.execute(
            select(EditProposal).where(
                EditProposal.state == ProposalState.PENDING.value,
                EditProposal.updated_at <= cutoff,
            )
        )
        .scalars()
        .all()
    )
    for proposal in proposals:
        proposal.state = ProposalState.FAILED.value
        proposal.failure_code = FAILURE_CODE
        proposal.failure_detail = FAILURE_DETAIL
    return len(proposals)


def _pending[RunT: (ReviewRun, ExportRun)](
    session: Session, model: type[RunT], cutoff: datetime
) -> Sequence[RunT]:
    return (
        session.execute(
            select(model).where(model.status == RunStatus.PENDING.value, model.updated_at <= cutoff)
        )
        .scalars()
        .all()
    )


def _fail_runs(runs: Sequence[ReviewRun] | Sequence[ExportRun]) -> int:
    for run in runs:
        run.status = RunStatus.FAILED.value
        run.failure_code = FAILURE_CODE
    return len(runs)


def _release_claims(session: Session, cutoff: datetime) -> int:
    claims = (
        session.execute(
            select(OperationRequest).where(
                OperationRequest.status == "IN_PROGRESS",
                OperationRequest.updated_at <= cutoff,
            )
        )
        .scalars()
        .all()
    )
    for claim in claims:
        session.delete(claim)
    return len(claims)
