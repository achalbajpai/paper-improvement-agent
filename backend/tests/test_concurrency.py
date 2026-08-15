"""What holds when the application's own locking is switched off.

Advisory locks make contention produce a clean typed error instead of a race.
They are also application code, and application code can be wrong: a lock taken
in the wrong order deadlocks, a lock forgotten protects nothing, and a refactor
can silently drop one. So the question these tests ask is not "does the locking
work" but "what happens when it does not" -- and the answer must be that the
database still refuses the second write.

Every test here disables `lock_paper` and `lock_proposal` and then runs the real
operation from several threads against real connections. The savepoint-based
`db` fixture cannot be used: it shares one connection, and a test of concurrency
that shares a connection is testing nothing. These commit for real and clean up
after themselves.

Two constraints carry the guarantees:

* `UNIQUE(accepted_proposal_id)` on `document_revisions` -- one revision per
  proposal, so double acceptance is an integrity error.
* `UNIQUE NULLS NOT DISTINCT` on `operation_requests` -- one winner per
  idempotency key, including for global-scope operations where `scope_id` is
  NULL and a plain unique index would let every row through.
"""

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
    """Remove the application's serialisation, leaving only the database's.

    Patched on the acceptance module's imported names as well as on the
    repository, because a `from x import y` binding is not affected by patching
    `x.y` and a half-applied patch would quietly leave the locking in place --
    making this suite pass for the wrong reason.
    """
    for module in ("app.db.repositories", "app.services.editor.acceptance"):
        for name in ("lock_paper", "lock_proposal"):
            monkeypatch.setattr(f"{module}.{name}", lambda *args, **kwargs: None, raising=False)


@pytest.fixture
def overlapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold every acceptance inside its transaction until two have arrived.

    A race test that never actually races is worse than no test: it passes for
    the wrong reason and reports a guarantee nobody verified. Blocking at the
    point where the revision number is chosen means both transactions have
    already passed the state, staleness, and snapshot checks, so what they
    collide on is the constraint itself.
    """
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
    """A real, committed paper. Removed afterwards, cascading to its children."""
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
    """Run `work` from `times` threads, each on its own connection.

    Results and exceptions are both returned so a test can assert on the shape of
    the failure rather than only on the count of survivors.
    """

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
    """The guarantee that survives the application's locking being wrong.

    Two threads accept the same proposal with nothing serialising them, and the
    `overlapping` fixture holds both inside the transaction until each has passed
    every check. Without that the threads could run one after the other and the
    loser would fail on the state check, which would leave the constraint --
    the thing actually under test -- never exercised.

    Exactly one revision may result, and the loser must fail rather than produce a
    second revision from the same decision.
    """
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
    """`UNIQUE(paper_id, revision_number)` decides who is revision 2.

    This used to race two accepted proposals. It no longer can: since
    `uq_one_active_proposal_per_paper` was added, a paper cannot hold two
    acceptable proposals at once, so that scenario is unreachable by
    construction. The revision-number constraint is still the backstop for any
    path that reaches a revision insert, so it is exercised directly rather than
    through a workflow the schema now forbids.
    """
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
    """The unique index is the arbiter, not a read-then-write in Python.

    Checking for an existing row and then inserting is a race with a window
    between the two statements, which is exactly the window a retried request
    lands in. The insert is allowed to fail and the loser reads the winner's row.
    """
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
    """Why the index must be NULLS NOT DISTINCT.

    Upload has no paper to scope to, so `scope_id` is NULL. Under the default
    NULLS DISTINCT, every NULL differs from every other NULL and a plain unique
    index would let four concurrent uploads all claim the same key -- the exact
    case idempotency exists for.
    """
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
    """What the advisory locks are actually for.

    The database guarantees correctness either way; the locks decide whether the
    loser sees a typed domain error or a raw integrity violation. A researcher
    should be told "this was already applied", not shown a constraint name.
    """
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
    """The read-then-insert overlap check is not a guarantee on its own.

    FastAPI runs sync handlers in a threadpool, so two requests genuinely
    interleave: both can see no active proposal before either has written one.
    The partial unique index is what makes "one live edit per paper" true, and
    it is what this asserts -- with the application's locking removed, so the
    database is answering on its own.
    """

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
    """The index must constrain live proposals only.

    A paper accumulates edits over its life. If the constraint counted settled
    ones, the second edit anybody ever made would be refused for ever.
    """
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
    """Two snapshots of one work would split its evidence.

    Evidence anchors address a span of *a* snapshot by character offset, so two
    rows for one abstract mean two findings quoting the same source can point at
    different rows -- and a later edit to one of them silently disagrees with
    the other.
    """
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
    """Ids travel through URLs and request bodies.

    A single-column foreign key would prove the revision exists; it would not
    prove it belongs to the paper the run is about. The composite key makes a
    cross-paper association unrepresentable rather than merely unlikely.
    """
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
    """The proposal's base snapshot must belong to the same paper.

    Service code currently selects the current revision server-side, but this is
    a persistence invariant: future import, recovery, or maintenance code must
    not be able to construct a cross-paper edit that acceptance would later
    treat as legitimate.
    """
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
