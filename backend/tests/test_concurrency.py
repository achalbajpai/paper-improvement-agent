from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import (
    DocumentRevision,
    EditProposal,
    OperationRequest,
    Paper,
    ReviewRun,
    SourceRecord,
)
from app.db.session import get_engine
from app.domain.errors import AppError
from app.domain.lifecycle import PaperStatus, RunStatus
from app.domain.proposal import ProposalState
from app.domain.source import ProviderName, ProviderWork
from app.services.citations.sources import SourceStore
from app.services.editor.acceptance import accept_proposal
from tests.conftest import validated
from tests.test_acceptance import stage

pytestmark = pytest.mark.usefixtures("database")


@pytest.fixture
def unlocked(monkeypatch: pytest.MonkeyPatch) -> None:
    for module in ("app.db.repositories", "app.services.editor.acceptance"):
        for name in ("lock_paper", "lock_proposal"):
            monkeypatch.setattr(f"{module}.{name}", lambda *args, **kwargs: None, raising=False)


@pytest.fixture
def overlapping(monkeypatch: pytest.MonkeyPatch) -> None:
    barrier = threading.Barrier(2, timeout=10)
    real = repositories.next_revision_number

    def synchronised(session: Session, paper_id: str) -> int:
        number = real(session, paper_id)
        barrier.wait()
        return number

    monkeypatch.setattr(
        "app.services.editor.acceptance.repositories.next_revision_number", synchronised
    )


@pytest.fixture
def committed_paper() -> Iterator[Paper]:
    document = validated("A_numeric").document
    with Session(get_engine()) as session:
        paper = Paper(
            id=repositories.new_id("paper"),
            storage_id=repositories.new_id("store"),
            original_filename="concurrency.pdf",
            content_sha256="2" * 64,
            size_bytes=1024,
            status=PaperStatus.PARSED.value,
            title=document.title or None,
        )
        session.add(paper)
        session.flush()
        revision = DocumentRevision(
            id=repositories.new_id("rev"),
            paper_id=paper.id,
            revision_number=1,
            document=document.model_dump(mode="json"),
            content_sha256=document.content_hash(),
            segmenter_version=document.segmenter_version,
        )
        session.add(revision)
        paper.current_revision_id = revision.id
        session.commit()
        paper_id = paper.id

    with Session(get_engine()) as session:
        yield session.get(Paper, paper_id)

    with Session(get_engine()) as session:
        session.execute(
            DocumentRevision.__table__.update()
            .where(DocumentRevision.paper_id == paper_id)
            .values(accepted_proposal_id=None)
        )
        session.commit()
        stale = session.get(Paper, paper_id)
        if stale is not None:
            session.delete(stale)
            session.commit()


def race(work: Callable[[Session], Any], times: int = 2) -> list[Any]:

    def attempt() -> Any:
        with Session(get_engine()) as session:
            try:
                return work(session)
            except Exception as error:
                session.rollback()
                return error

    with ThreadPoolExecutor(max_workers=times) as pool:
        return [future.result() for future in [pool.submit(attempt) for _ in range(times)]]


def test_double_acceptance_is_refused_by_the_database(
    committed_paper: Paper, unlocked: None, overlapping: None
) -> None:
    with Session(get_engine()) as session:
        paper = session.get(Paper, committed_paper.id)
        assert paper is not None
        proposal = stage(session, paper)
        session.commit()
        proposal_id = proposal.id

    outcomes = race(
        lambda session: accept_proposal(session, proposal_id, acknowledged_warning_ids=[])
    )

    succeeded = [item for item in outcomes if isinstance(item, DocumentRevision)]
    failed = [item for item in outcomes if isinstance(item, Exception)]
    assert len(succeeded) == 1
    assert len(failed) == 1
    assert isinstance(failed[0], IntegrityError)

    with Session(get_engine()) as session:
        revisions = session.scalar(
            select(func.count())
            .select_from(DocumentRevision)
            .where(DocumentRevision.accepted_proposal_id == proposal_id)
        )
        assert revisions == 1
        assert session.get(EditProposal, proposal_id).state == ProposalState.ACCEPTED.value


def test_two_revisions_cannot_take_the_same_number(committed_paper: Paper, unlocked: None) -> None:
    number_seen: list[int] = []
    barrier = threading.Barrier(2, timeout=10)

    def insert(session: Session) -> Any:
        number = repositories.next_revision_number(session, committed_paper.id)
        number_seen.append(number)
        barrier.wait()
        revision = DocumentRevision(
            id=repositories.new_id("rev"),
            paper_id=committed_paper.id,
            revision_number=number,
            document={},
            content_sha256="0" * 64,
            segmenter_version="seg/v1",
        )
        session.add(revision)
        session.commit()
        return revision.id

    outcomes = race(insert)

    assert number_seen == [2, 2], "the two writers did not actually collide"
    assert len([item for item in outcomes if not isinstance(item, Exception)]) == 1
    assert any(isinstance(item, IntegrityError) for item in outcomes)

    with Session(get_engine()) as session:
        numbers = session.scalars(
            select(DocumentRevision.revision_number).where(
                DocumentRevision.paper_id == committed_paper.id
            )
        ).all()
        assert sorted(numbers) == [1, 2]


def race_over(subjects: list[str], work: Callable[[Session, str], Any]) -> list[Any]:
    def attempt(subject: str) -> Any:
        with Session(get_engine()) as session:
            try:
                return work(session, subject)
            except Exception as error:
                session.rollback()
                return error

    with ThreadPoolExecutor(max_workers=len(subjects)) as pool:
        return [future.result() for future in [pool.submit(attempt, s) for s in subjects]]


def test_one_idempotency_key_produces_one_claim(committed_paper: Paper) -> None:
    key = str(uuid.uuid4())

    def claim(session: Session) -> Any:
        outcome = repositories.begin_operation(
            session,
            scope_type="paper",
            scope_id=committed_paper.id,
            operation_type="PARSE_PAPER",
            idempotency_key=key,
            request_body={"paper_id": committed_paper.id},
        )
        session.commit()
        return outcome

    race(claim, times=4)

    with Session(get_engine()) as session:
        rows = session.scalar(
            select(func.count())
            .select_from(OperationRequest)
            .where(OperationRequest.idempotency_key == key)
        )
        assert rows == 1
        session.execute(
            OperationRequest.__table__.delete().where(OperationRequest.idempotency_key == key)
        )
        session.commit()


def test_a_global_scope_key_is_still_unique(committed_paper: Paper) -> None:
    key = str(uuid.uuid4())

    def claim(session: Session) -> Any:
        outcome = repositories.begin_operation(
            session,
            scope_type="global",
            scope_id=None,
            operation_type="UPLOAD_PAPER",
            idempotency_key=key,
            request_body={"filename": "paper.pdf"},
        )
        session.commit()
        return outcome

    race(claim, times=4)

    with Session(get_engine()) as session:
        rows = session.scalar(
            select(func.count())
            .select_from(OperationRequest)
            .where(OperationRequest.idempotency_key == key)
        )
        assert rows == 1
        session.execute(
            OperationRequest.__table__.delete().where(OperationRequest.idempotency_key == key)
        )
        session.commit()


def test_locking_turns_the_race_into_a_clean_refusal(committed_paper: Paper) -> None:
    with Session(get_engine()) as session:
        paper = session.get(Paper, committed_paper.id)
        assert paper is not None
        proposal_id = stage(session, paper).id
        session.commit()

    outcomes = race(
        lambda session: accept_proposal(session, proposal_id, acknowledged_warning_ids=[])
    )
    failures = [item for item in outcomes if isinstance(item, Exception)]

    assert len([item for item in outcomes if isinstance(item, DocumentRevision)]) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], AppError)


def test_two_proposals_cannot_both_hold_the_edit_slot(
    committed_paper: Paper, unlocked: None
) -> None:

    def insert(session: Session) -> Any:
        proposal = EditProposal(
            id=repositories.new_id("prop"),
            paper_id=committed_paper.id,
            base_revision_id=committed_paper.current_revision_id or "",
            command="shorten the introduction",
            state=ProposalState.PENDING.value,
        )
        session.add(proposal)
        session.commit()
        return proposal.id

    outcomes = race(insert)
    survived = [item for item in outcomes if not isinstance(item, Exception)]
    refused = [item for item in outcomes if isinstance(item, IntegrityError)]

    assert len(survived) == 1
    assert len(refused) == 1

    with Session(get_engine()) as session:
        active = session.execute(
            select(func.count())
            .select_from(EditProposal)
            .where(
                EditProposal.paper_id == committed_paper.id,
                EditProposal.state.in_(("PENDING", "AWAITING_DECISION")),
            )
        ).scalar_one()
    assert active == 1


def test_a_settled_proposal_frees_the_slot(committed_paper: Paper) -> None:
    with Session(get_engine()) as session:
        for state in (ProposalState.ACCEPTED, ProposalState.REJECTED, ProposalState.FAILED):
            session.add(
                EditProposal(
                    id=repositories.new_id("prop"),
                    paper_id=committed_paper.id,
                    base_revision_id=committed_paper.current_revision_id or "",
                    command=f"a {state.value} edit",
                    state=state.value,
                )
            )
        session.commit()

        session.add(
            EditProposal(
                id=repositories.new_id("prop"),
                paper_id=committed_paper.id,
                base_revision_id=committed_paper.current_revision_id or "",
                command="a new edit",
                state=ProposalState.PENDING.value,
            )
        )
        session.commit()


def test_one_work_is_snapshotted_once_under_contention(
    committed_paper: Paper, unlocked: None
) -> None:
    work = ProviderWork(
        provider=ProviderName.OPENALEX,
        external_id="W_contended",
        title="A contended work",
        abstract="One sentence. Another sentence.",
    )

    def snapshot(session: Session) -> Any:
        record_id = SourceStore(session, committed_paper.id).snapshot(work)
        session.commit()
        return record_id

    outcomes = race(snapshot)
    assert not [item for item in outcomes if isinstance(item, Exception)], outcomes
    assert len(set(outcomes)) == 1, "the same work was snapshotted under two ids"

    with Session(get_engine()) as session:
        stored = session.execute(
            select(func.count())
            .select_from(SourceRecord)
            .where(SourceRecord.paper_id == committed_paper.id)
        ).scalar_one()
    assert stored == 1


def test_a_run_cannot_cite_another_papers_revision(committed_paper: Paper) -> None:
    with Session(get_engine()) as session:
        other = Paper(
            id=repositories.new_id("paper"),
            storage_id=repositories.new_id("store"),
            original_filename="other.pdf",
            content_sha256="7" * 64,
            size_bytes=1024,
            status=PaperStatus.PARSED.value,
        )
        session.add(other)
        session.commit()
        other_id = other.id

    try:
        with Session(get_engine()) as session:
            session.add(
                ReviewRun(
                    id=repositories.new_id("rev"),
                    paper_id=other_id,
                    revision_id=committed_paper.current_revision_id or "",
                    status=RunStatus.PENDING.value,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        with Session(get_engine()) as session:
            stale = session.get(Paper, other_id)
            if stale is not None:
                session.delete(stale)
                session.commit()


def test_a_proposal_cannot_edit_another_papers_revision(committed_paper: Paper) -> None:
    with Session(get_engine()) as session:
        other = Paper(
            id=repositories.new_id("paper"),
            storage_id=repositories.new_id("store"),
            original_filename="other.pdf",
            content_sha256="8" * 64,
            size_bytes=1024,
            status=PaperStatus.PARSED.value,
        )
        session.add(other)
        session.commit()
        other_id = other.id

    try:
        with Session(get_engine()) as session:
            session.add(
                EditProposal(
                    id=repositories.new_id("prop"),
                    paper_id=other_id,
                    base_revision_id=committed_paper.current_revision_id or "",
                    command="Shorten the introduction",
                    state=ProposalState.PENDING.value,
                )
            )
            with pytest.raises(IntegrityError):
                session.commit()
    finally:
        with Session(get_engine()) as session:
            stale = session.get(Paper, other_id)
            if stale is not None:
                session.delete(stale)
                session.commit()
