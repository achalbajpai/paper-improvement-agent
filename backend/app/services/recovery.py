"""Reconciling work that was interrupted rather than failed.

Every operation persists its own terminal state, including for exceptions it did
not anticipate. That covers the process staying alive long enough to write the
row. It does not cover the process not staying alive: a container restart, an
OOM kill, or a host dying mid-parse leaves a ``PARSING`` paper or a ``PENDING``
proposal that no handler will ever return to.

Those states are not merely untidy, they are terminal in the wrong direction. A
paper stuck in ``PARSING`` cannot be parsed again, and a proposal stuck in
``PENDING`` blocks every future edit on its paper: the researcher's only recourse
would be to re-upload.

So on startup, anything left running by a process that is no longer here is
marked failed with a code that says why. This is a sweep, not a scheduler: these
operations are synchronous, so nothing can legitimately be running when the
process that would be running it has only just started.
"""

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
    """Mark interrupted work terminal, so the researcher is never locked out.

    ``older_than`` exists for tests and for the paranoid: at startup the default
    of zero is correct, because no synchronous operation of this process can
    predate this process.
    """
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
    """A PENDING proposal holds the paper's one edit slot until it is resolved."""
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
    """Idempotency claims left open by a process that is no longer running.

    An unresolved claim answers OPERATION_IN_PROGRESS to every retry, for ever.
    The key the client will retry with is the one it already used, so leaving
    the row would make that operation permanently unrepeatable -- the opposite
    of what idempotency is for.

    Deleted rather than marked, which is what ``fail_operation`` does for a
    handled failure: the operation did not complete, so the correct answer to a
    retry is to run it, not to replay a result that was never produced.
    """
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
