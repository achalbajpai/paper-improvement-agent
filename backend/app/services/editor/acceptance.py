"""Applying a proposal, once and only once.

Everything before this point is reversible: a proposal is a document sitting in a
column that nothing reads. Acceptance is the one operation that moves the paper,
so it is the one operation that has to be exactly right under concurrency.

The transaction takes the **paper lock first, then the proposal lock**, always in
that order. Two operations that take the same two locks in opposite orders
deadlock, and the pair that would do it here is real: acceptance walks paper to
proposal, and a stale-proposal sweep walks proposal to paper.

Re-checking at acceptance is not paranoia about our own code. The researcher read
a candidate, thought about it, and clicked accept some minutes later, and in
between the paper may have moved. So the base revision is re-compared, the
snapshot's hash is re-derived, and the acknowledgements are re-matched against the
warnings that are required *now*.

The stored revision is the snapshot's document. The edit is never re-run: if it
were, the researcher's decision and the stored result could differ, and the whole
point of a candidate revision is that they cannot.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import DocumentRevision, EditProposal
from app.domain.errors import (
    AcknowledgementRequiredError,
    CandidateSnapshotError,
    ProposalStateError,
    StaleRevisionError,
    VerificationBlockedError,
)
from app.domain.proposal import CandidateRevisionSnapshot, ProposalState
from app.observability import get_logger

logger = get_logger(__name__)


def accept_proposal(
    session: Session,
    proposal_id: str,
    *,
    acknowledged_warning_ids: list[str],
) -> DocumentRevision:
    """Apply a proposal as a new immutable revision.

    Either every check passes and one revision appears, or nothing changes.
    """
    proposal = repositories.get_proposal(session, proposal_id)
    paper_id = proposal.paper_id

    repositories.lock_paper(session, paper_id)
    repositories.lock_proposal(session, proposal_id)

    session.refresh(proposal)
    paper = repositories.get_paper(session, paper_id)

    if proposal.state != ProposalState.AWAITING_DECISION.value:
        raise ProposalStateError(
            "Only a proposal awaiting a decision can be accepted.",
            proposal_id=proposal_id,
            state=proposal.state,
        )
    if proposal.candidate is None:
        raise CandidateSnapshotError(
            "This proposal has no candidate revision to apply.", proposal_id=proposal_id
        )

    snapshot = CandidateRevisionSnapshot.model_validate(proposal.candidate)

    if snapshot.snapshot_sha256 != proposal.candidate_sha256:
        raise CandidateSnapshotError(
            "The stored candidate does not match the snapshot that was verified.",
            proposal_id=proposal_id,
        )
    if proposal.base_revision_id != paper.current_revision_id:
        raise StaleRevisionError(
            "The paper has changed since this edit was proposed, so it no longer applies.",
            proposal_id=proposal_id,
            base_revision_id=proposal.base_revision_id,
            current_revision_id=paper.current_revision_id,
        )
    if snapshot.base_revision_id != paper.current_revision_id:
        raise StaleRevisionError(
            "The candidate was computed against a revision that is no longer current.",
            proposal_id=proposal_id,
        )
    if snapshot.verification.is_blocked:
        raise VerificationBlockedError(
            "This edit was blocked by a safety check and cannot be accepted.",
            proposal_id=proposal_id,
            blockers=[blocker.code.value for blocker in snapshot.verification.blockers],
        )

    _require_acknowledgements(proposal, snapshot, acknowledged_warning_ids)

    revision = DocumentRevision(
        id=repositories.new_id("rev"),
        paper_id=paper_id,
        revision_number=repositories.next_revision_number(session, paper_id),
        document=snapshot.document.model_dump(mode="json"),
        content_sha256=snapshot.document.content_hash(),
        parent_revision_id=proposal.base_revision_id,
        accepted_proposal_id=proposal.id,
        segmenter_version=snapshot.document.segmenter_version,
    )
    session.add(revision)
    session.flush()

    paper.current_revision_id = revision.id
    proposal.state = ProposalState.ACCEPTED.value
    proposal.acknowledged_warning_ids = sorted(set(acknowledged_warning_ids))
    proposal.decided_at = datetime.now(UTC)

    _supersede_others(session, paper_id, proposal.id)
    session.commit()

    logger.info(
        "proposal.accepted",
        extra={
            "paper_id": paper_id,
            "proposal_id": proposal.id,
            "revision_id": revision.id,
            "revision_number": revision.revision_number,
        },
    )
    return revision


def reject_proposal(session: Session, proposal_id: str) -> EditProposal:
    """Discard a proposal. The manuscript is untouched either way."""
    proposal = repositories.get_proposal(session, proposal_id)
    repositories.lock_paper(session, proposal.paper_id)
    repositories.lock_proposal(session, proposal_id)
    session.refresh(proposal)

    if proposal.state != ProposalState.AWAITING_DECISION.value:
        raise ProposalStateError(
            "Only a proposal awaiting a decision can be rejected.",
            proposal_id=proposal_id,
            state=proposal.state,
        )

    proposal.state = ProposalState.REJECTED.value
    proposal.decided_at = datetime.now(UTC)
    session.commit()
    return proposal


def _require_acknowledgements(
    proposal: EditProposal,
    snapshot: CandidateRevisionSnapshot,
    acknowledged: list[str],
) -> None:
    """Every warning must be acknowledged, and by the id it has *now*.

    The required set is re-derived from the snapshot rather than read from the
    row, and warning ids are content-derived, so an acknowledgement collected
    against a different candidate does not match anything here. That is the
    property that makes the checklist meaningful: it cannot be satisfied by
    remembering what the last one said.
    """
    required = set(snapshot.verification.required_warning_ids)
    stored = set(proposal.required_warning_ids)
    if stored and stored != required:
        raise CandidateSnapshotError(
            "The warnings recorded for this proposal do not match its candidate.",
            proposal_id=proposal.id,
        )

    missing = sorted(required - set(acknowledged))
    if missing:
        raise AcknowledgementRequiredError(
            "This edit has consequences that must be acknowledged before it is applied.",
            proposal_id=proposal.id,
            missing_warning_ids=missing,
        )


def _supersede_others(session: Session, paper_id: str, accepted_id: str) -> None:
    """Proposals built against the revision this one just replaced.

    They were computed against a document that is no longer current, so they are
    marked superseded rather than left to fail confusingly at acceptance.

    Blocked proposals included: a blocked candidate is just as stale as a live
    one, and leaving it blocked would keep showing a diff against a revision
    that no longer exists.
    """
    for other in repositories.unsettled_proposals(session, paper_id):
        if other.id != accepted_id:
            other.state = ProposalState.SUPERSEDED.value
            other.decided_at = datetime.now(UTC)
