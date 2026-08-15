"""Review runs, as they reach the database and come back out.

The property that matters here is that findings persist as *anchors*, and every
quoted string in the response is re-derived from the revision and from the
snapshotted abstract. A stored review carrying its own copy of the prose would
drift the moment anything else changed.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.presenters import review_run
from app.api.presenters.reviews import _evidence
from app.db import repositories
from app.db.models import (
    DocumentRevision,
    Paper,
    RetrievalAttempt,
    ReviewFindingRow,
    ReviewRun,
    SourceRecord,
)
from app.db.repositories import new_id
from app.domain.document import Document
from app.domain.lifecycle import RunStatus
from app.domain.reference import ResolutionConfidence, ResolutionMethod
from app.domain.review import FindingKind, SupportVerdict
from app.domain.source import ProviderName, ProviderWork, RetrievalPurpose
from app.main import app
from app.providers.session import AttemptRecord, ProviderSession
from app.services.citations.resolver import Resolution
from app.services.citations.sources import SourceStore, record_attempts
from app.services.deadline import Deadline
from app.services.review.runner import (
    ReviewRunner,
    claim_run,
    persist,
    record_failure,
)
from tests.test_review import (
    ABSTRACT,
    StubResolver,
    StubRetrieval,
    resolved_work,
    scripted,
    uncited_work,
)


def run_and_persist(
    db: Session, paper: Paper, suggestions: list[ProviderWork] | None = None
) -> ReviewRun:
    revision = db.get(DocumentRevision, paper.current_revision_id)
    document = Document.model_validate(revision.document if revision else {})
    provider_session = ProviderSession(operation_id="op_test")
    runner = ReviewRunner(db, paper.id, llm=scripted(), provider_session=provider_session)
    runner.resolver = StubResolver(
        Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, resolved_work())
    )
    runner.missing_work.retrieval = StubRetrieval(suggestions or [])
    run_id = claim_run(db, paper_id=paper.id, revision_id=paper.current_revision_id or "").id
    outcome = runner.run(document, Deadline.after("test", 60.0))
    return persist(
        db,
        run_id=run_id,
        paper_id=paper.id,
        outcome=outcome,
        provider_session=provider_session,
    )


def test_a_completed_run_persists_its_findings(db: Session, stored_paper: Paper) -> None:
    presented = review_run(db, run_and_persist(db, stored_paper))

    assert presented.status.value == "COMPLETED"
    assert presented.findings
    assert presented.stats["paragraphs_reviewed"] > 0


def test_findings_store_anchors_and_not_prose(db: Session, stored_paper: Paper) -> None:
    run = run_and_persist(db, stored_paper)
    stored = db.query(ReviewFindingRow).filter_by(run_id=run.id).all()

    for row in stored:
        assert "text" not in row.claim_anchor
        for anchor in row.evidence:
            assert set(anchor) == {
                "source_record_id",
                "span_id",
                "char_start",
                "char_end",
                "text_hash",
            }


def test_claim_text_is_recovered_from_the_revision(db: Session, stored_paper: Paper) -> None:
    presented = review_run(db, run_and_persist(db, stored_paper))
    quoted = [finding for finding in presented.findings if finding.claim.text]

    assert quoted
    assert all(finding.claim.section_id for finding in quoted)


def test_evidence_text_comes_from_the_snapshotted_abstract(
    db: Session, stored_paper: Paper
) -> None:
    presented = review_run(db, run_and_persist(db, stored_paper))
    with_evidence = [finding for finding in presented.findings if finding.evidence]

    assert with_evidence
    for finding in with_evidence:
        for span in finding.evidence:
            assert span.text
            assert span.text in ABSTRACT


def test_verdicts_survive_the_round_trip(db: Session, stored_paper: Paper) -> None:
    presented = review_run(db, run_and_persist(db, stored_paper))
    support = [f for f in presented.findings if f.kind is FindingKind.CITATION_SUPPORT]

    assert support
    assert all(isinstance(finding.verdict, SupportVerdict) for finding in support)


def test_a_failed_run_still_leaves_a_row(db: Session, stored_paper: Paper) -> None:
    """ "The review errored" and "the review found nothing" are different states."""
    run = record_failure(
        db,
        run_id=new_id("rev"),
        paper_id=stored_paper.id,
        revision_id=stored_paper.current_revision_id or "",
        failure_code="LLM_UNAVAILABLE",
        provider_session=ProviderSession(operation_id="op_test"),
    )
    presented = review_run(db, run)

    assert presented.status.value == "FAILED"
    assert presented.failure_code is not None
    assert presented.findings == []


def test_a_work_is_snapshotted_once_however_often_it_is_cited(
    db: Session, stored_paper: Paper
) -> None:
    store = SourceStore(db, stored_paper.id)

    first = store.snapshot(resolved_work())
    second = store.snapshot(resolved_work())

    assert first == second
    assert db.query(SourceRecord).filter_by(paper_id=stored_paper.id).count() == 1


def test_a_snapshot_is_never_refreshed(db: Session, stored_paper: Paper) -> None:
    """Evidence must keep meaning what it meant when the verdict was made.

    A provider silently editing an abstract would move every span offset, and
    quoted evidence would drift away from the claim it was about.
    """
    record_id = SourceStore(db, stored_paper.id).snapshot(
        resolved_work().model_copy(update={"abstract": ABSTRACT})
    )
    SourceStore(db, stored_paper.id).snapshot(
        resolved_work().model_copy(update={"abstract": "a rewritten abstract"})
    )

    stored = db.get(SourceRecord, record_id)
    assert stored is not None
    assert stored.abstract == ABSTRACT


def test_an_absent_abstract_is_filled_in_later(db: Session, stored_paper: Paper) -> None:
    """Filling a gap is not overwriting: no anchor can point into a NULL."""
    record_id = SourceStore(db, stored_paper.id).snapshot(
        resolved_work().model_copy(update={"abstract": None})
    )
    SourceStore(db, stored_paper.id).snapshot(
        resolved_work().model_copy(update={"abstract": ABSTRACT})
    )

    stored = db.get(SourceRecord, record_id)
    assert stored is not None
    assert stored.abstract == ABSTRACT


def test_provider_attempts_are_recorded_against_the_paper(db: Session, stored_paper: Paper) -> None:
    """Query text is manuscript-derived, so it lives in a paper-scoped row that
    cascades on delete, and never in a log line."""
    provider_session = ProviderSession(operation_id="op_test")
    provider_session.record(
        AttemptRecord(
            provider=ProviderName.OPENALEX,
            purpose=RetrievalPurpose.RESOLVE_REFERENCE,
            query="doi:10.1/x",
            status="OK",
            failure_code=None,
            result_count=1,
            latency_ms=8,
        )
    )
    record_attempts(db, stored_paper.id, provider_session)

    rows = db.query(RetrievalAttempt).filter_by(paper_id=stored_paper.id).all()
    assert len(rows) == 1
    assert rows[0].query == "doi:10.1/x"
    assert provider_session.attempts == []


def test_only_https_provider_links_reach_the_ui(db: Session, stored_paper: Paper) -> None:
    """Provider metadata is untrusted output."""
    record_id = SourceStore(db, stored_paper.id).snapshot(
        ProviderWork(
            provider=ProviderName.OPENALEX,
            external_id="W_insecure",
            title="A work",
            url="javascript:alert(1)",
        )
    )
    record = db.get(SourceRecord, record_id)
    assert record is not None

    span = _evidence(
        {"source_record_id": record_id, "span_id": "span_000", "char_start": 0, "char_end": 0},
        {record_id: record},
    )
    assert span.source_url is None


def test_a_suggestion_round_trips_as_a_linkable_source(db: Session, stored_paper: Paper) -> None:
    """A missing-work finding is only worth anything if the work is reachable.

    The finding stores source record ids; the presenter reads title, authors and
    link back out of the snapshot. Nothing about the suggested work is carried in
    the finding row itself, so a snapshot that vanished cannot leave a
    confident-looking recommendation pointing at nothing.
    """
    run = run_and_persist(db, stored_paper, suggestions=[uncited_work()])
    presented = review_run(db, run)

    missing = [f for f in presented.findings if f.kind is FindingKind.MISSING_WORK]
    assert missing, "no missing-work finding was produced"
    for finding in missing:
        assert finding.suggestions
        for suggestion in finding.suggestions:
            assert suggestion.title
            assert suggestion.url and suggestion.url.startswith("https://")
            assert db.get(SourceRecord, suggestion.source_record_id) is not None


def test_a_suggestion_whose_snapshot_is_gone_is_not_rendered(
    db: Session, stored_paper: Paper
) -> None:
    run = run_and_persist(db, stored_paper, suggestions=[uncited_work()])
    for record in db.query(SourceRecord).filter(SourceRecord.paper_id == stored_paper.id).all():
        db.delete(record)
    db.flush()

    presented = review_run(db, run)
    assert all(not f.suggestions for f in presented.findings)


def test_snapshotting_leaves_no_transaction_open(db: Session, stored_paper: Paper) -> None:
    """The caller's next act is a model call that can take tens of seconds.

    A snapshot that left its transaction open would pin a pooled connection for
    the length of the review, which is what the operation's phase structure
    exists to prevent -- and what this module's docstring promises.
    """
    store = SourceStore(db, stored_paper.id)

    store.snapshot(resolved_work())
    assert not db.in_transaction() or db.get_transaction() is None or not db.dirty

    store.snapshot(resolved_work().model_copy(update={"external_id": "W2"}))
    assert not db.dirty and not db.new


def test_a_model_authored_verdict_records_who_made_it(db: Session, stored_paper: Paper) -> None:
    """A judgement nobody can trace back is one nobody can re-examine.

    The provider is part of it, not decoration: two providers serve models under
    the same name, and a configured fallback can answer a call the primary was
    configured for. Without it a stored verdict cannot be attributed even in
    principle.
    """
    run = run_and_persist(db, stored_paper, suggestions=[uncited_work()])
    rows = db.query(ReviewFindingRow).filter_by(run_id=run.id).all()

    judged = [row for row in rows if row.kind == FindingKind.CITATION_SUPPORT.value and row.verdict]
    model_authored = [
        row
        for row in judged
        if row.verdict in {SupportVerdict.SUPPORTED.value, SupportVerdict.PARTIALLY_SUPPORTED.value}
    ]
    assert model_authored, "no model-authored verdict was produced"

    for row in model_authored:
        assert row.provider, f"{row.id} records no provider"
        assert row.model
        assert row.prompt_version

    presented = review_run(db, run)
    presented_by_id = {finding.id: finding for finding in presented.findings}
    for row in model_authored:
        finding = presented_by_id[row.id]
        assert finding.model_provider == row.provider
        assert finding.model == row.model
        assert finding.prompt_version == row.prompt_version

    suggested = [row for row in rows if row.kind == FindingKind.MISSING_WORK.value]
    for row in suggested:
        assert row.provider and row.model and row.prompt_version


def test_a_server_decided_verdict_claims_no_model(db: Session, stored_paper: Paper) -> None:
    """Unresolved and evidence-unavailable are the server's own conclusions.

    Attaching a provider to them would credit a model with a decision it was
    never asked to make.
    """
    run = run_and_persist(db, stored_paper)
    rows = db.query(ReviewFindingRow).filter_by(run_id=run.id).all()

    for row in rows:
        if row.verdict in {
            SupportVerdict.SOURCE_UNRESOLVED.value,
            SupportVerdict.EVIDENCE_UNAVAILABLE.value,
            SupportVerdict.SOURCE_IDENTITY_UNCERTAIN.value,
        }:
            assert row.provider is None, f"{row.id} credits a model with a server decision"


def test_a_finding_can_be_marked_handled_and_reopened(db: Session, stored_paper: Paper) -> None:
    """Triage records that the researcher read a finding.

    A full-coverage run produces hundreds, so working through them needs a way to
    say "dealt with". It is presentation only: the finding stays in the run and
    stays in the run's counts, because removing it from its own denominator would
    be the reporting failure this system exists to avoid.
    """
    run = ReviewRun(
        id=repositories.new_id("run"),
        paper_id=stored_paper.id,
        revision_id=stored_paper.current_revision_id,
        status=RunStatus.COMPLETED.value,
        degradations=[],
        stats={},
    )
    db.add(run)
    row = ReviewFindingRow(
        id=repositories.new_id("find"),
        run_id=run.id,
        paper_id=stored_paper.id,
        kind=FindingKind.UNCITED_CLAIM.value,
        claim_anchor={"paragraph_id": "p_1_1", "sentence_id": "s1", "sentence_index": 0},
        evidence=[],
        reason="",
        suggested_source_record_ids=[],
        suggestion_rationales=[],
    )
    db.add(row)
    db.commit()

    app.dependency_overrides[get_session] = lambda: db
    try:
        client = TestClient(app)
        assert client.patch(f"/findings/{row.id}", json={"handled": True}).json()["handled"] is True
        assert (
            client.patch(f"/findings/{row.id}", json={"handled": False}).json()["handled"] is False
        )

        body = client.get(f"/reviews/{run.id}").json()
        assert len(body["findings"]) == 1, "triage must not remove the finding from its run"
    finally:
        app.dependency_overrides.clear()


def test_handling_an_unknown_finding_is_a_not_found() -> None:
    assert TestClient(app).patch("/findings/find_nope", json={"handled": True}).status_code == 404
