from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import DocumentRevision, EditProposal, Paper
from app.domain.delta import ComputedEditDelta
from app.domain.document import Document
from app.domain.errors import (
    AcknowledgementRequiredError,
    CandidateSnapshotError,
    ProposalStateError,
    StaleRevisionError,
    VerificationBlockedError,
)
from app.domain.proposal import CandidateRevisionSnapshot, ProposalState
from app.domain.verification import (
    BlockerCode,
    VerificationBlocker,
    VerificationResult,
    VerificationWarning,
    WarningCode,
)
from app.services.editor.acceptance import accept_proposal, reject_proposal
from app.services.editor.candidate_revision import CandidateRevisionBuilder
from app.services.editor.delta_engine import compute_delta
from app.services.parser.segmenter import detokenize, tokenize

pytestmark = pytest.mark.usefixtures("database")


def edited(document: Document) -> tuple[Document, ComputedEditDelta]:
    paragraph = next(iter(document.paragraphs()))
    builder = CandidateRevisionBuilder(document)
    text = tokenize(paragraph)
    builder.replace_paragraph(paragraph.id, text[: max(40, len(text) // 2)].rsplit(" ", 1)[0])
    candidate = builder.build()
    return candidate, compute_delta(document, candidate)


def stage(
    db: Session,
    paper: Paper,
    *,
    warnings: tuple[VerificationWarning, ...] = (),
    blockers: tuple[VerificationBlocker, ...] = (),
    state: ProposalState = ProposalState.AWAITING_DECISION,
    base_revision_id: str | None = None,
) -> EditProposal:
    revision = repositories.get_current_revision(db, paper)
    document = Document.model_validate(revision.document)
    candidate, delta = edited(document)
    snapshot = CandidateRevisionSnapshot(
        base_revision_id=base_revision_id or revision.id,
        document=candidate,
        delta=delta,
        verification=VerificationResult(warnings=warnings, blockers=blockers),
    )
    proposal = EditProposal(
        id=repositories.new_id("prop"),
        paper_id=paper.id,
        base_revision_id=base_revision_id or revision.id,
        command="Shorten the introduction",
        intent="SHORTEN_SECTION",
        state=state.value,
        candidate=snapshot.model_dump(mode="json"),
        candidate_sha256=snapshot.snapshot_sha256,
        required_warning_ids=list(snapshot.verification.required_warning_ids),
    )
    db.add(proposal)
    db.flush()
    return proposal


def a_warning(subject: str = "cite_imported_001") -> VerificationWarning:
    return VerificationWarning.build(
        WarningCode.CITATION_REMOVED,
        "The citation [1] will no longer appear in the manuscript.",
        (subject,),
        "content",
    )


def test_acceptance_creates_one_revision_and_moves_the_paper(
    db: Session, stored_paper: Paper
) -> None:
    proposal = stage(db, stored_paper)
    before = repositories.get_current_revision(db, stored_paper).id

    revision = accept_proposal(db, proposal.id, acknowledged_warning_ids=[])

    assert revision.revision_number == 2
    assert revision.parent_revision_id == before
    assert revision.accepted_proposal_id == proposal.id
    assert stored_paper.current_revision_id == revision.id
    assert proposal.state == ProposalState.ACCEPTED.value


def test_the_stored_revision_is_the_snapshot_not_a_re_run(db: Session, stored_paper: Paper) -> None:
    proposal = stage(db, stored_paper)
    snapshot = CandidateRevisionSnapshot.model_validate(proposal.candidate)

    revision = accept_proposal(db, proposal.id, acknowledged_warning_ids=[])

    assert revision.document == snapshot.document.model_dump(mode="json")
    assert revision.content_sha256 == snapshot.document.content_hash()


def test_an_unacknowledged_warning_stops_acceptance(db: Session, stored_paper: Paper) -> None:
    warning = a_warning()
    proposal = stage(db, stored_paper, warnings=(warning,))

    with pytest.raises(AcknowledgementRequiredError) as caught:
        accept_proposal(db, proposal.id, acknowledged_warning_ids=[])

    assert caught.value.details["missing_warning_ids"] == [warning.id]
    assert proposal.state == ProposalState.AWAITING_DECISION.value


def test_acknowledging_a_different_warning_does_not_count(db: Session, stored_paper: Paper) -> None:
    proposal = stage(db, stored_paper, warnings=(a_warning("cite_imported_001"),))
    other = a_warning("cite_imported_099")

    with pytest.raises(AcknowledgementRequiredError):
        accept_proposal(db, proposal.id, acknowledged_warning_ids=[other.id])


def test_acknowledging_the_warning_lets_it_through(db: Session, stored_paper: Paper) -> None:
    warning = a_warning()
    proposal = stage(db, stored_paper, warnings=(warning,))

    revision = accept_proposal(db, proposal.id, acknowledged_warning_ids=[warning.id])

    assert revision.revision_number == 2
    assert proposal.acknowledged_warning_ids == [warning.id]


def test_a_blocked_proposal_cannot_be_accepted_however_much_is_acknowledged(
    db: Session, stored_paper: Paper
) -> None:
    blocker = VerificationBlocker(
        code=BlockerCode.UNSUPPORTED_NOVELTY,
        message="The shortened text asserts something the original does not.",
    )
    proposal = stage(db, stored_paper, blockers=(blocker,), state=ProposalState.BLOCKED)
    proposal.state = ProposalState.AWAITING_DECISION.value
    db.flush()

    with pytest.raises(VerificationBlockedError):
        accept_proposal(db, proposal.id, acknowledged_warning_ids=["anything"])


def test_a_proposal_built_against_an_older_revision_is_refused(
    db: Session, stored_paper: Paper
) -> None:
    first = stage(db, stored_paper)
    accept_proposal(db, first.id, acknowledged_warning_ids=[])

    stale = stage(db, stored_paper, base_revision_id=first.base_revision_id)

    with pytest.raises(StaleRevisionError):
        accept_proposal(db, stale.id, acknowledged_warning_ids=[])


def test_accepting_twice_is_refused(db: Session, stored_paper: Paper) -> None:
    proposal = stage(db, stored_paper)
    accept_proposal(db, proposal.id, acknowledged_warning_ids=[])

    with pytest.raises(ProposalStateError):
        accept_proposal(db, proposal.id, acknowledged_warning_ids=[])

    assert db.query(DocumentRevision).filter_by(accepted_proposal_id=proposal.id).count() == 1


def test_a_tampered_candidate_is_refused(db: Session, stored_paper: Paper) -> None:
    proposal = stage(db, stored_paper)
    candidate: dict[str, Any] = dict(proposal.candidate or {})
    document = dict(candidate["document"])
    document["title"] = "A different paper entirely"
    candidate["document"] = document
    proposal.candidate = candidate
    db.flush()

    with pytest.raises(CandidateSnapshotError):
        accept_proposal(db, proposal.id, acknowledged_warning_ids=[])


def test_accepting_one_proposal_supersedes_the_others(db: Session, stored_paper: Paper) -> None:
    first = stage(db, stored_paper)
    second = stage(db, stored_paper, state=ProposalState.BLOCKED)

    accept_proposal(db, first.id, acknowledged_warning_ids=[])

    assert second.state == ProposalState.SUPERSEDED.value


def test_rejecting_leaves_the_manuscript_alone(db: Session, stored_paper: Paper) -> None:
    proposal = stage(db, stored_paper)
    before = stored_paper.current_revision_id

    reject_proposal(db, proposal.id)

    assert proposal.state == ProposalState.REJECTED.value
    assert stored_paper.current_revision_id == before


def test_a_rejected_proposal_cannot_then_be_accepted(db: Session, stored_paper: Paper) -> None:
    proposal = stage(db, stored_paper)
    reject_proposal(db, proposal.id)

    with pytest.raises(ProposalStateError):
        accept_proposal(db, proposal.id, acknowledged_warning_ids=[])


def test_the_edit_actually_reaches_the_new_revision(db: Session, stored_paper: Paper) -> None:
    proposal = stage(db, stored_paper)
    base = Document.model_validate(repositories.get_current_revision(db, stored_paper).document)

    revision = accept_proposal(db, proposal.id, acknowledged_warning_ids=[])
    applied = Document.model_validate(revision.document)

    assert applied.word_count() < base.word_count()
    assert {p.id for p in applied.paragraphs()} == {p.id for p in base.paragraphs()}
    assert detokenize(tokenize(next(iter(applied.paragraphs())))) != detokenize(
        tokenize(next(iter(base.paragraphs())))
    )
