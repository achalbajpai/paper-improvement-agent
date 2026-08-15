"""Interrupted work, and the states it must never be left in.

Two failure modes, tested separately because they have different fixes.

An exception the code did not anticipate is handled *in* the operation: the
handler catches everything, not only ``AppError``, so the terminal state is
written before the exception leaves. A process that dies mid-operation cannot be
handled there at all, and is swept at startup instead.

Both matter for the same reason: ``PARSING`` and ``PENDING`` are not merely
untidy, they are refusing states. A paper stuck in ``PARSING`` cannot be
re-parsed, and a proposal stuck in ``PENDING`` blocks every future edit on its
paper.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import EditProposal, ExportRun, OperationRequest, Paper, ReviewRun
from app.db.repositories import new_id
from app.domain.errors import (
    AppError,
    ErrorCode,
    InternalError,
    OperationInProgressError,
    as_app_error,
)
from app.domain.lifecycle import PaperStatus, RunStatus
from app.domain.proposal import ProposalState
from app.services.parser import pipeline
from app.services.recovery import reconcile_interrupted
from app.services.review.runner import claim_run
from tests.conftest import load_tei


def test_an_unexpected_exception_gets_a_typed_identity() -> None:
    typed = as_app_error(KeyError("p_1_1"))

    assert isinstance(typed, InternalError)
    assert typed.code is ErrorCode.INTERNAL_ERROR


def test_normalising_never_puts_the_exception_text_in_the_envelope() -> None:
    """An unexpected exception's message can quote manuscript prose."""
    typed = as_app_error(ValueError("The dominant sequence transduction models are"))

    rendered = str(typed.envelope())
    assert "sequence transduction" not in rendered
    assert typed.details == {"cause": "ValueError"}


def test_a_typed_error_is_passed_through_unchanged() -> None:
    original = InternalError("already typed")
    assert as_app_error(original) is original


def test_an_unexpected_parse_failure_still_leaves_the_paper_parseable(
    db: Session, stored_paper: Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paper left in PARSING can never be parsed again.

    ``_claim_for_parsing`` admits UPLOADED, PARSE_FAILED and PARSED only, so a
    crash that skipped the failure path would take the paper out of the product
    for good.
    """

    def explode(*args: object, **kwargs: object) -> str:
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(pipeline, "_run_grobid", explode)

    with pytest.raises(RuntimeError):
        pipeline.parse_paper(db, stored_paper.id, operation_id=new_id("op"))

    paper = db.get(Paper, stored_paper.id)
    assert paper is not None
    assert paper.status == PaperStatus.PARSE_FAILED.value
    assert paper.failure_code == ErrorCode.INTERNAL_ERROR.value
    assert paper.active_parse_op_id is None


def test_a_failure_while_committing_the_parse_is_also_terminal(
    db: Session, stored_paper: Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The commit sits inside the handled block, not after it."""

    def explode(*args: object, **kwargs: object) -> object:
        raise RuntimeError("commit failed")

    monkeypatch.setattr(pipeline, "_commit_parse", explode)
    monkeypatch.setattr(pipeline, "_run_grobid", lambda *a, **k: load_tei("A_numeric").decode())

    with pytest.raises((AppError, RuntimeError)):
        pipeline.parse_paper(db, stored_paper.id, operation_id=new_id("op"))

    paper = db.get(Paper, stored_paper.id)
    assert paper is not None
    assert paper.status != PaperStatus.PARSING.value
    assert paper.active_parse_op_id is None


def test_an_interrupted_parse_is_swept_at_startup(db: Session, stored_paper: Paper) -> None:
    stored_paper.status = PaperStatus.PARSING.value
    stored_paper.active_parse_op_id = "op_gone"
    db.flush()

    counts = reconcile_interrupted(db)

    assert counts["papers"] == 1
    assert stored_paper.status == PaperStatus.PARSE_FAILED.value
    assert stored_paper.active_parse_op_id is None
    assert stored_paper.failure_detail


def test_an_interrupted_proposal_stops_blocking_the_paper(db: Session, stored_paper: Paper) -> None:
    """A PENDING proposal holds the paper's one edit slot for ever."""
    proposal = EditProposal(
        id=new_id("prop"),
        paper_id=stored_paper.id,
        base_revision_id=stored_paper.current_revision_id or "",
        command="shorten the introduction",
        state=ProposalState.PENDING.value,
    )
    db.add(proposal)
    db.flush()

    counts = reconcile_interrupted(db)

    assert counts["proposals"] == 1
    assert proposal.state == ProposalState.FAILED.value
    assert proposal.failure_code == ErrorCode.INTERNAL_ERROR.value


def test_interrupted_runs_are_swept(db: Session, stored_paper: Paper) -> None:
    review = ReviewRun(
        id=new_id("rev"),
        paper_id=stored_paper.id,
        revision_id=stored_paper.current_revision_id or "",
        status=RunStatus.PENDING.value,
    )
    export = ExportRun(
        id=new_id("exp"),
        paper_id=stored_paper.id,
        revision_id=stored_paper.current_revision_id or "",
        citation_style="IEEE",
        status=RunStatus.PENDING.value,
    )
    db.add_all([review, export])
    db.flush()

    counts = reconcile_interrupted(db)

    assert counts["reviews"] == 1
    assert counts["exports"] == 1
    assert review.status == RunStatus.FAILED.value
    assert export.status == RunStatus.FAILED.value


def test_the_sweep_leaves_settled_work_alone(db: Session, stored_paper: Paper) -> None:
    """A completed run swept into FAILED would be a worse bug than the one this fixes."""
    counts = reconcile_interrupted(db)

    assert counts == {"papers": 0, "proposals": 0, "reviews": 0, "exports": 0, "claims": 0}
    assert stored_paper.status == PaperStatus.PARSED.value


def test_an_abandoned_idempotency_claim_stops_burning_the_key(
    db: Session, stored_paper: Paper
) -> None:
    """An unresolved claim answers OPERATION_IN_PROGRESS to every retry, for ever.

    The client retries with the key it already used, so a claim left open by a
    dead process makes that operation permanently unrepeatable.
    """
    outcome = repositories.begin_operation(
        db,
        scope_type="paper",
        scope_id=stored_paper.id,
        operation_type="RUN_REVIEW",
        idempotency_key="key-abandoned",
        request_body={"paper_id": stored_paper.id},
    )
    assert isinstance(outcome, OperationRequest)
    db.flush()

    with pytest.raises(OperationInProgressError):
        repositories.begin_operation(
            db,
            scope_type="paper",
            scope_id=stored_paper.id,
            operation_type="RUN_REVIEW",
            idempotency_key="key-abandoned",
            request_body={"paper_id": stored_paper.id},
        )

    assert reconcile_interrupted(db)["claims"] == 1

    retried = repositories.begin_operation(
        db,
        scope_type="paper",
        scope_id=stored_paper.id,
        operation_type="RUN_REVIEW",
        idempotency_key="key-abandoned",
        request_body={"paper_id": stored_paper.id},
    )
    assert isinstance(retried, OperationRequest)


def test_a_review_killed_mid_run_leaves_a_row_to_settle(db: Session, stored_paper: Paper) -> None:
    """The run row is written before the slow work, not after it.

    Written only on completion, a review killed during its minutes of provider
    and model calls would leave nothing: no failed run for the researcher, and
    nothing for the sweep to find.
    """
    run = claim_run(
        db, paper_id=stored_paper.id, revision_id=stored_paper.current_revision_id or ""
    )
    db.flush()
    assert run.status == RunStatus.PENDING.value

    assert reconcile_interrupted(db)["reviews"] == 1
    assert run.status == RunStatus.FAILED.value
    assert run.failure_code == ErrorCode.INTERNAL_ERROR.value
