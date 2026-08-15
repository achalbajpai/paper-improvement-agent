"""Grounded review.

Two families of test. The first checks the grounding boundary directly: what
happens when a model returns an identifier nobody gave it. The second runs the
whole review over a real parsed paper with a scripted model and stubbed
providers, and checks the properties that make a verdict trustworthy -- one
verdict per (claim, occurrence, reference), server-owned quoted text, and source
state decided by the server rather than by the model.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from pydantic import ValidationError

from app.domain.errors import GroundingValidationError
from app.domain.reference import ResolutionConfidence, ResolutionMethod
from app.domain.review import FindingKind, SupportVerdict
from app.domain.source import ProviderName, ProviderWork
from app.providers.session import ProviderSession
from app.services.citations.bibliography import BibliographyIndex
from app.services.citations.context_extractor import split_abstract
from app.services.citations.resolver import Resolution
from app.services.deadline import Deadline
from app.services.parser.segmenter import SEGMENTER_VERSION
from app.services.research.allowlist import Allowlist, require_choice
from app.services.research.retrieval import Candidate, VerifiedCandidateSet
from app.services.review.missing_work import MissingWorkReviewer, paragraphs_worth_searching
from app.services.review.runner import ReviewRunner, _paragraphs_worth_reviewing
from app.services.review.support import SupportResponse
from tests.conftest import validated
from tests.support.llm import ScriptedLLM

ABSTRACT = (
    "We introduce Optuna, a hyperparameter optimisation framework. "
    "It uses define-by-run APIs to construct search spaces dynamically. "
    "Experiments show it outperforms existing frameworks on several tasks."
)


def test_an_unknown_identifier_raises_rather_than_being_dropped() -> None:
    """The line that stops an invented citation.

    Silently discarding the unknown id would let a half-invented response through
    labelled as if it were sound.
    """
    allowlist = Allowlist("sentence", {"sent_a": "one", "sent_b": "two"})

    with pytest.raises(GroundingValidationError) as caught:
        allowlist.resolve("sent_invented", prompt="claims")

    assert caught.value.details["kind"] == "sentence"
    assert caught.value.details["returned_id"] == "sent_invented"


def test_resolve_all_is_all_or_nothing() -> None:
    allowlist = Allowlist("span", {"span_000": "text"})
    with pytest.raises(GroundingValidationError):
        allowlist.resolve_all(["span_000", "span_999"], prompt="support")


def test_a_verdict_outside_the_permitted_set_is_refused() -> None:
    """UNSUPPORTED is deliberately absent, and that boundary is enforced here
    rather than trusted to a provider honouring ``strict: true``."""
    with pytest.raises(GroundingValidationError):
        require_choice(
            "UNSUPPORTED",
            [verdict.value for verdict in SupportVerdict][:4],
            prompt="support",
            field="verdict",
        )


def test_support_evidence_is_bounded_to_prevent_runaway_model_output() -> None:
    with pytest.raises(ValidationError):
        SupportResponse.model_validate(
            {
                "verdict": "SUPPORTED",
                "evidence_span_ids": [f"span_{index:03d}" for index in range(4)],
                "reason": "Too many spans.",
            }
        )


def test_the_error_reports_the_id_and_not_the_manuscript() -> None:
    allowlist = Allowlist("sentence", {"sent_a": "A confidential unpublished finding."})
    with pytest.raises(GroundingValidationError) as caught:
        allowlist.resolve("sent_x", prompt="claims")

    rendered = str(caught.value.details)
    assert "confidential" not in rendered


def test_evidence_spans_index_into_the_snapshotted_abstract() -> None:
    """The server quotes; the model only points."""
    spans = split_abstract(ABSTRACT)

    assert len(spans) == 3
    for span in spans:
        assert ABSTRACT[span.char_start : span.char_end].strip() == span.text


def test_an_empty_abstract_yields_no_spans_rather_than_one_empty_one() -> None:
    assert split_abstract("") == ()
    assert split_abstract("   ") == ()


class StubResolver:
    """Stands in for the ladder so a review test does not depend on a provider."""

    def __init__(self, resolution: Resolution, abstract: str | None = ABSTRACT) -> None:
        self._resolution = resolution
        self._abstract = abstract
        self.resolved: list[str] = []

    def resolve(self, reference: Any, deadline: Deadline) -> Resolution:
        self.resolved.append(reference.id)
        return self._resolution

    def fetch_abstract(self, work: ProviderWork, deadline: Deadline) -> ProviderWork:
        return work.model_copy(update={"abstract": self._abstract})


def resolved_work() -> ProviderWork:
    return ProviderWork(
        provider=ProviderName.OPENALEX,
        external_id="W1",
        title="A cited work",
        year=2019,
        authors=("Ada Lovelace",),
        doi="10.1/x",
    )


def scripted(verdict: str = "SUPPORTED", span_ids: list[str] | None = None) -> ScriptedLLM:
    """A model that calls every offered sentence a claim.

    Deliberately maximal: it exercises every downstream path rather than letting
    a selective stand-in quietly skip the interesting citations.
    """
    return ScriptedLLM(
        {
            "claims": lambda prompt: {
                "claims": [
                    {"sentence_id": sentence_id, "is_citable_claim": True, "topic": "the topic"}
                    for sentence_id in _offered_sentence_ids(prompt.user)
                ]
            },
            "support": {
                "verdict": verdict,
                "evidence_span_ids": span_ids if span_ids is not None else ["span_000"],
                "reason": "The abstract states this directly.",
            },
            "rerank": lambda prompt: {
                "ranked": [
                    {
                        "candidate_id": candidate_id,
                        "rank": index + 1,
                        "rationale": "Directly on this claim's topic.",
                    }
                    for index, candidate_id in enumerate(_offered_candidate_ids(prompt.user))
                ]
            },
        }
    )


_SENTENCE_LINE = re.compile(r"^(p_\d+_\d+:s\d+):")
_CANDIDATE_LINE = re.compile(r"^(cand_\d+):")


def _offered_candidate_ids(user_prompt: str) -> list[str]:
    return [
        match.group(1)
        for line in user_prompt.splitlines()
        if (match := _CANDIDATE_LINE.match(line))
    ]


def _offered_sentence_ids(user_prompt: str) -> list[str]:
    """Read back exactly the ids the server put in the prompt.

    A stand-in that invented its own ids would be testing the grounding
    validator rather than the review.
    """
    return [
        match.group(1) for line in user_prompt.splitlines() if (match := _SENTENCE_LINE.match(line))
    ]


class NullStore:
    """Snapshotting is exercised in its own test; here it only issues ids."""

    def __init__(self) -> None:
        self.snapshots: list[ProviderWork] = []

    def snapshot(self, work: ProviderWork) -> str:
        self.snapshots.append(work)
        return "src_001"


class StubRetrieval:
    """Stands in for both providers, returning a fixed candidate set."""

    def __init__(self, works: list[ProviderWork] | None = None) -> None:
        self._works = works or []
        self.queries: list[str] = []

    def search(self, query: str, deadline: Deadline, *, limit: int | None = None) -> Any:
        self.queries.append(query)
        return VerifiedCandidateSet(
            query=query,
            candidates=[
                Candidate(id=f"cand_{index:03d}", work=work, csl=work.to_csl(f"cand_{index:03d}"))
                for index, work in enumerate(self._works)
            ],
        )


def build_runner(
    llm: ScriptedLLM,
    resolution: Resolution,
    abstract: str | None = ABSTRACT,
    retrieval: StubRetrieval | None = None,
) -> ReviewRunner:
    provider_session = ProviderSession(operation_id="op_test")
    store = NullStore()

    missing = MissingWorkReviewer.__new__(MissingWorkReviewer)
    missing.session = None
    missing.paper_id = "paper_test"
    missing.llm = llm
    missing.provider_session = provider_session
    missing.sources = store
    missing.retrieval = retrieval or StubRetrieval()

    runner = ReviewRunner.__new__(ReviewRunner)
    runner.session = None
    runner.paper_id = "paper_test"
    runner.llm = llm
    runner.provider_session = provider_session
    runner.resolver = StubResolver(resolution, abstract)
    runner.sources = store
    runner.missing_work = missing
    runner._resolutions = {}
    runner._records = {}
    runner._claims = {}
    return runner


@pytest.fixture(scope="module")
def document() -> Any:
    return validated("A_numeric").document


def run_review(runner: ReviewRunner, document: Any) -> Any:
    return runner.run(document, Deadline.after("test", 60.0))


def test_one_verdict_per_claim_occurrence_reference_triple(document: Any) -> None:
    """``[2, 5]`` is two assertions of support, not one.

    Collapsing them would hide which of the two is weak, which is the only
    actionable part of the finding.
    """
    runner = build_runner(
        scripted(), Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, resolved_work())
    )
    outcome = run_review(runner, document)

    support = [f for f in outcome.findings if f.kind is FindingKind.CITATION_SUPPORT]
    assert support
    keys = [(f.claim_anchor.sentence_id, f.occurrence_id, f.reference_id) for f in support]
    assert len(keys) == len(set(keys))

    multi = [f for f in support if f.occurrence_id]
    by_occurrence: dict[str, set[str]] = {}
    for finding in multi:
        by_occurrence.setdefault(finding.occurrence_id or "", set()).add(finding.reference_id or "")
    assert any(len(references) > 1 for references in by_occurrence.values()), (
        "the corpus paper has a multi-reference marker; each reference needs its own verdict"
    )


def test_evidence_is_anchored_never_quoted_by_the_model(document: Any) -> None:
    runner = build_runner(
        scripted(), Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, resolved_work())
    )
    outcome = run_review(runner, document)

    anchored = [f for f in outcome.findings if f.evidence]
    assert anchored
    for finding in anchored:
        for anchor in finding.evidence:
            assert anchor.source_record_id == "src_001"
            assert anchor.char_end > anchor.char_start
            assert ABSTRACT[anchor.char_start : anchor.char_end].strip()


def test_an_uncertain_resolution_never_becomes_a_support_verdict(document: Any) -> None:
    """A verdict against a paper we merely suspect is the cited one is worse
    than admitting we do not know which paper it is."""
    runner = build_runner(
        scripted(),
        Resolution(ResolutionMethod.TITLE_ONLY, ResolutionConfidence.UNCERTAIN, resolved_work()),
    )
    outcome = run_review(runner, document)

    verdicts = {f.verdict for f in outcome.findings if f.kind is FindingKind.CITATION_SUPPORT}
    assert verdicts == {SupportVerdict.SOURCE_IDENTITY_UNCERTAIN}
    assert "support" not in runner.llm.calls


def test_an_unresolved_reference_is_reported_as_source_state(document: Any) -> None:
    runner = build_runner(scripted(), Resolution.unresolved())
    outcome = run_review(runner, document)

    support = [f for f in outcome.findings if f.kind is FindingKind.CITATION_SUPPORT]
    assert {f.verdict for f in support} == {SupportVerdict.SOURCE_UNRESOLVED}
    assert any(f.kind is FindingKind.UNRESOLVED_REFERENCE for f in outcome.findings)


def test_a_work_with_no_abstract_is_evidence_unavailable_not_unsupported(document: Any) -> None:
    """Failing to find support is not evidence that support is absent."""
    runner = build_runner(
        scripted(),
        Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, resolved_work()),
        abstract=None,
    )
    outcome = run_review(runner, document)

    support = [f for f in outcome.findings if f.kind is FindingKind.CITATION_SUPPORT]
    assert {f.verdict for f in support} == {SupportVerdict.EVIDENCE_UNAVAILABLE}


def test_a_fabricated_span_id_discards_its_response_without_killing_the_run(
    document: Any,
) -> None:
    """A fabricated id costs its own response, and nothing else.

    The response is still refused whole -- no finding built on an invented span
    reaches the researcher. What changed is the blast radius: aborting the run
    meant one bad reply discarded every sound paragraph with it, and over a whole
    manuscript a rejection is near-certain, so the strict reading made
    full-manuscript review impossible rather than safe.

    The rejection is counted, and the assertions that response would have
    produced stay uncounted -- unassessed, which the coverage panel already
    refuses to report as a pass.
    """
    runner = build_runner(
        scripted(span_ids=["span_never_issued"]),
        Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, resolved_work()),
    )
    outcome = run_review(runner, document)

    assert outcome.stats["grounding_rejections"] > 0
    assert not [
        finding
        for finding in outcome.findings
        if any(anchor.span_id == "span_never_issued" for anchor in finding.evidence)
    ]
    assert outcome.stats["support_assertions_completed"] < outcome.stats["support_assertions_total"]


def test_a_reference_is_resolved_once_however_often_it_is_cited(document: Any) -> None:
    """Resolution is the expensive part; citing a work twice must not pay twice."""
    runner = build_runner(
        scripted(), Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, resolved_work())
    )
    run_review(runner, document)

    resolved = runner.resolver.resolved
    assert len(resolved) == len(set(resolved))


def test_claim_anchors_carry_the_segmenter_version(document: Any) -> None:
    """An anchor compared across segmenter versions points at the wrong sentence."""
    runner = build_runner(
        scripted(), Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, resolved_work())
    )
    outcome = run_review(runner, document)

    versions = {
        f.claim_anchor.segmenter_version for f in outcome.findings if f.claim_anchor.sentence_id
    }
    assert versions == {SEGMENTER_VERSION}


def test_the_run_reports_how_much_it_covered(document: Any) -> None:
    runner = build_runner(
        scripted(), Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, resolved_work())
    )
    outcome = run_review(runner, document)

    assert outcome.stats["paragraphs_reviewed"] > 0
    assert outcome.stats["references_examined"] >= outcome.stats["references_resolved"]
    assert (
        outcome.stats["support_assertions_completed"] == outcome.stats["support_assertions_total"]
    )
    assert outcome.stats["missing_work_paragraphs_searched"] > 0


def uncited_work(external_id: str = "W_new", title: str = "An uncited framework") -> ProviderWork:
    return ProviderWork(
        provider=ProviderName.OPENALEX,
        external_id=external_id,
        title=title,
        year=2021,
        authors=("Grace Hopper",),
        doi=f"10.9/{external_id}",
        url=f"https://openalex.org/{external_id}",
    )


def missing_work_runner(works: list[ProviderWork]) -> tuple[ReviewRunner, StubRetrieval]:
    retrieval = StubRetrieval(works)
    runner = build_runner(
        scripted(),
        Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, resolved_work()),
        retrieval=retrieval,
    )
    return runner, retrieval


def test_missing_work_is_searched_and_reported_with_its_sources(document: Any) -> None:
    """The half of peer review that finds what the author did not cite.

    Without this the review can only grade citations that already exist, which
    is not the question a researcher asks of a reviewer.
    """
    runner, retrieval = missing_work_runner([uncited_work()])
    outcome = run_review(runner, document)

    assert retrieval.queries, "the reviewer never searched a provider"
    findings = [f for f in outcome.findings if f.kind is FindingKind.MISSING_WORK]
    assert findings
    for finding in findings:
        assert finding.suggested_source_record_ids
        assert finding.claim_anchor.sentence_id
    assert outcome.stats["works_suggested"] == sum(
        len(f.suggested_source_record_ids) for f in findings
    )


def test_a_suggestion_is_never_a_work_already_in_the_bibliography(document: Any) -> None:
    """Recommending a paper the author already cites is confidently wrong."""
    cited = next(reference for reference in document.references if reference.csl.title)
    already = ProviderWork(
        provider=ProviderName.OPENALEX,
        external_id="W_dup",
        title=cited.csl.title,
        year=2019,
        authors=("Ada Lovelace",),
        doi=cited.csl.DOI,
        url="https://openalex.org/W_dup",
    )
    runner, _ = missing_work_runner([already])
    outcome = run_review(runner, document)

    assert not [f for f in outcome.findings if f.kind is FindingKind.MISSING_WORK]


def test_a_work_with_no_link_is_never_suggested(document: Any) -> None:
    """A suggestion the researcher cannot open is not a recommendation."""
    unlinkable = ProviderWork(
        provider=ProviderName.OPENALEX,
        external_id="W_nolink",
        title="A work with no locator",
        year=2021,
        authors=("Grace Hopper",),
    )
    runner, _ = missing_work_runner([unlinkable])
    outcome = run_review(runner, document)

    assert not [f for f in outcome.findings if f.kind is FindingKind.MISSING_WORK]


def test_an_uncited_claim_says_what_the_search_found(document: Any) -> None:
    """ "No citation here" alone invites the reader to assume nothing was looked for."""
    runner, _ = missing_work_runner([])
    outcome = run_review(runner, document)

    uncited = [f for f in outcome.findings if f.kind is FindingKind.UNCITED_CLAIM]
    if not uncited:
        pytest.skip("this corpus paper cites every claim the extractor selected")
    assert any("searched" in f.reason.lower() for f in uncited)


def test_search_selection_does_not_prefer_densely_cited_paragraphs(document: Any) -> None:
    """Missing work is likeliest where citations are sparse.

    The support pass orders by citation density because that is where a verdict
    has something to check. Reusing that ordering here would search hardest
    exactly where there is least to find.
    """
    searched = paragraphs_worth_searching(document, 8)
    reviewed = _paragraphs_worth_reviewing(document, 8)

    densities = [len(paragraph.citation_ids) for paragraph in searched]
    assert densities != sorted(densities, reverse=True) or len(set(densities)) == 1
    assert [p.id for p in searched] != [p.id for p in reviewed]


def test_the_bibliography_index_matches_on_title_when_identifiers_differ(document: Any) -> None:
    """GROBID recovers a title far more often than an identifier."""
    index = BibliographyIndex.of(document)
    title = next(r.csl.title for r in document.references if r.csl.title)

    same = ProviderWork(
        provider=ProviderName.SEMANTIC_SCHOLAR,
        external_id="S_other",
        title=title,
    )
    assert index.contains(same)


def test_the_manuscript_is_never_suggested_to_its_own_author(document: Any) -> None:
    """A published paper is in the providers' indexes.

    A search on the paper's own topic returns the paper itself, and telling an
    author to cite their own manuscript is the most obviously wrong suggestion
    this system could make. Seen on the first live run against OpenAlex.
    """
    itself = ProviderWork(
        provider=ProviderName.OPENALEX,
        external_id="W_self",
        title=document.title,
        year=2016,
        authors=("Kaiming He",),
        doi="10.1109/cvpr.2016.90",
        url="https://doi.org/10.1109/cvpr.2016.90",
    )
    runner, _ = missing_work_runner([itself])
    outcome = run_review(runner, document)

    assert not [f for f in outcome.findings if f.kind is FindingKind.MISSING_WORK]
