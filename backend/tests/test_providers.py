from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.domain.errors import (
    LLMRateLimitedError,
    LLMUnavailableError,
    ProviderBudgetExhaustedError,
    ProviderInvalidResponseError,
    ProviderNotFoundError,
    ProviderQueryRejectedError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from app.domain.source import ProviderName, ProviderWork, RetrievalPurpose
from app.providers.cache import ProviderCache
from app.providers.http import RequestPacer
from app.providers.llm import LLMClient, Prompt
from app.providers.openalex import (
    OpenAlexClient,
    _filter_safe,
    normalise_arxiv,
    normalise_doi,
    reconstruct_abstract,
)
from app.providers.semantic_scholar import SemanticScholarClient
from app.providers.session import AttemptRecord, ProviderSession
from app.services.deadline import Deadline
from app.services.research.retrieval import AcademicRetrievalService, _deduplicate_works
from app.services.review.support import SupportResponse
from app.settings import get_settings

FIXTURES = Path(__file__).parent / "fixtures" / "providers"


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_openalex_normalisation_from_live_payload() -> None:
    work = OpenAlexClient.to_work(fixture("openalex_by_doi"))

    assert work.provider is ProviderName.OPENALEX
    assert work.external_id == "W2949676527"
    assert work.doi == "10.1145/3292500.3330701"
    assert work.year == 2019
    assert work.authors[0] == "Takuya Akiba"
    assert work.work_type == "paper-conference"
    assert work.pages == "2623-2631"


def test_openalex_abstract_is_reconstructed_from_the_inverted_index() -> None:
    work = OpenAlexClient.to_work(fixture("openalex_by_doi"))
    assert work.has_abstract
    assert work.abstract is not None
    assert work.abstract.startswith("The purpose of this study is")


def test_inverted_index_reconstruction_orders_by_position() -> None:
    assert reconstruct_abstract({"world": [1], "hello": [0]}) == "hello world"
    assert reconstruct_abstract({"the": [0, 2], "cat": [1]}) == "the cat the"
    assert reconstruct_abstract({}) is None
    assert reconstruct_abstract(None) is None


@pytest.mark.parametrize(
    "raw",
    [
        "10.1145/3292500.3330701",
        "https://doi.org/10.1145/3292500.3330701",
        "http://dx.doi.org/10.1145/3292500.3330701",
        "doi:10.1145/3292500.3330701",
        "  10.1145/3292500.3330701.  ",
    ],
)
def test_doi_normalisation_collapses_every_shape(raw: str) -> None:
    assert normalise_doi(raw) == "10.1145/3292500.3330701"


def test_non_dois_are_rejected_rather_than_passed_through() -> None:
    assert normalise_doi("") is None
    assert normalise_doi(None) is None
    assert normalise_doi("arXiv:1907.10902") is None


def test_semantic_scholar_normalisation_from_live_payload() -> None:
    work = SemanticScholarClient.to_work(fixture("s2_by_doi"))

    assert work.provider is ProviderName.SEMANTIC_SCHOLAR
    assert work.external_id == "4cdf2fad22afc865999747336c7399fe422e6e8e"
    assert work.doi == "10.1145/3292500.3330701"
    assert work.arxiv_id == "1907.10902"
    assert work.year == 2019
    assert work.has_abstract


def test_the_two_providers_disagree_about_this_work_s_title() -> None:
    openalex = OpenAlexClient.to_work(fixture("openalex_by_doi"))
    s2 = SemanticScholarClient.to_work(fixture("s2_by_doi"))

    assert openalex.title == "Optuna"
    assert s2.title.startswith("Optuna: A Next-generation")
    assert openalex.doi == s2.doi


def test_both_providers_produce_the_same_identity_key_for_one_work() -> None:
    openalex = OpenAlexClient.to_work(fixture("openalex_by_doi"))
    s2 = SemanticScholarClient.to_work(fixture("s2_by_doi"))
    assert openalex.identity_keys()[0] == s2.identity_keys()[0]


def test_cross_provider_candidates_are_deduplicated_even_with_asymmetric_ids() -> None:
    openalex = OpenAlexClient.to_work(fixture("openalex_by_doi"))
    s2 = SemanticScholarClient.to_work(fixture("s2_by_doi")).model_copy(
        update={"doi": None, "arxiv_id": "1706.03762", "title": openalex.title}
    )

    assert len(_deduplicate_works([openalex, s2])) == 1


def test_provider_work_becomes_a_csl_item_whose_id_matches_the_reference() -> None:
    work = OpenAlexClient.to_work(fixture("openalex_by_doi"))
    csl = work.to_csl("ref_new_001")

    assert csl.id == "ref_new_001"
    assert csl.year == 2019
    assert csl.DOI == "10.1145/3292500.3330701"
    assert csl.is_complete_for_insertion()


def test_a_work_without_a_year_is_not_complete_enough_to_insert() -> None:
    bare = ProviderWork(
        provider=ProviderName.OPENALEX, external_id="W1", title="A paper", authors=("Ada",)
    )
    assert not bare.to_csl("ref_new_002").is_complete_for_insertion()


def test_cache_returns_a_stored_value_and_expires_it() -> None:
    cache = ProviderCache()
    key = cache.key("OPENALEX", "RESOLVE_REFERENCE", "doi:10.1/x")
    cache.put(key, {"hit": True})
    assert cache.get(key) == {"hit": True}


def test_cache_is_case_insensitive_on_the_argument() -> None:
    cache = ProviderCache()
    cache.put(cache.key("OPENALEX", "SEARCH_CANDIDATES", "Attention Is All You Need"), [1])
    assert cache.get(cache.key("OPENALEX", "SEARCH_CANDIDATES", "attention is all you need")) == [1]


def test_cache_exposes_no_way_to_store_a_failure() -> None:
    code = ProviderCache.put.__code__
    names = code.co_varnames[: code.co_argcount + code.co_kwonlyargcount]
    assert set(names) == {"self", "key", "value", "empty"}


def make_session() -> ProviderSession:
    return ProviderSession(operation_id="op_test")


def test_degradation_is_sticky_for_the_rest_of_the_operation() -> None:
    session = make_session()
    session.degrade(
        ProviderName.SEMANTIC_SCHOLAR,
        ProviderRateLimitedError("rate limited", provider="SEMANTIC_SCHOLAR"),
    )

    assert session.is_degraded(ProviderName.SEMANTIC_SCHOLAR)
    assert not session.is_degraded(ProviderName.OPENALEX)
    with pytest.raises(ProviderRateLimitedError):
        session.claim_call(ProviderName.SEMANTIC_SCHOLAR)


def test_the_first_degradation_wins() -> None:
    session = make_session()
    session.degrade(
        ProviderName.SEMANTIC_SCHOLAR, ProviderRateLimitedError("limited", provider="S2")
    )
    session.degrade(ProviderName.SEMANTIC_SCHOLAR, ProviderUnavailableError("down", provider="S2"))

    degradations = session.degradations
    assert len(degradations) == 1
    assert degradations[0].code == "PROVIDER_RATE_LIMITED"


def test_degradation_is_always_reportable() -> None:
    session = make_session()
    session.degrade(ProviderName.OPENALEX, ProviderUnavailableError("down", provider="OPENALEX"))
    reported = session.degradation_for(ProviderName.OPENALEX)
    assert reported is not None
    assert reported.provider == "OPENALEX"


def test_the_call_budget_is_enforced_rather_than_advisory(monkeypatch: Any) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "max_openalex_calls_per_operation", 2)
    monkeypatch.setattr(settings, "max_provider_calls_per_operation", 40)

    session = make_session()
    session.claim_call(ProviderName.OPENALEX)
    session.claim_call(ProviderName.OPENALEX)
    with pytest.raises(ProviderBudgetExhaustedError):
        session.claim_call(ProviderName.OPENALEX)


def test_http_attempts_cannot_overrun_the_reserved_operation_budget(monkeypatch: Any) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "max_provider_calls_per_operation", 1)
    session = make_session()
    record_attempt = session.attempt_recorder(ProviderName.SEMANTIC_SCHOLAR)

    session.claim_call(ProviderName.SEMANTIC_SCHOLAR)
    record_attempt()
    with pytest.raises(ProviderBudgetExhaustedError):
        record_attempt()

    assert session.total_calls == 1


def test_request_pacer_serialises_one_provider_quota(monkeypatch: Any) -> None:
    now = [100.0]
    sleeps: list[float] = []

    monkeypatch.setattr("app.providers.http.time.monotonic", lambda: now[0])

    def advance(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("app.providers.http.time.sleep", advance)
    pacer = RequestPacer()

    assert pacer.acquire("SEMANTIC_SCHOLAR", min_interval=1.05, timeout=5.0) == 5.0
    remaining = pacer.acquire("SEMANTIC_SCHOLAR", min_interval=1.05, timeout=5.0)

    assert sleeps == [pytest.approx(1.05)]
    assert remaining == pytest.approx(3.95)


def test_request_pacer_refuses_to_outlive_the_call_deadline(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.providers.http.time.monotonic", lambda: 100.0)
    pacer = RequestPacer()
    pacer.acquire("OPENALEX", min_interval=0.11, timeout=1.0)

    with pytest.raises(ProviderRateLimitedError):
        pacer.acquire("OPENALEX", min_interval=0.11, timeout=0.05)


def test_one_provider_quota_never_serialises_another() -> None:
    pacer = RequestPacer()

    assert pacer._quota_lock("OPENALEX") is pacer._quota_lock("OPENALEX")
    assert pacer._quota_lock("OPENALEX") is not pacer._quota_lock("SEMANTIC_SCHOLAR")


def test_attempts_are_recorded_for_the_paper_scoped_log() -> None:
    session = make_session()
    session.record(
        AttemptRecord(
            provider=ProviderName.OPENALEX,
            purpose=RetrievalPurpose.SEARCH_CANDIDATES,
            query="title:something",
            status="OK",
            failure_code=None,
            result_count=3,
            latency_ms=12,
        )
    )
    assert len(session.attempts) == 1
    assert session.attempts[0].result_count == 3


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Overfeat: Integrated recognition, localization and detection",
            "Overfeat Integrated recognition localization and detection",
        ),
        ("A Multigrid Tutorial", "A Multigrid Tutorial"),
        ("Learning (deep) features!", "Learning deep features"),
    ],
)
def test_a_title_is_stripped_of_openalex_filter_syntax(title: str, expected: str) -> None:
    assert _filter_safe(title) == expected


def test_the_cache_key_distinguishes_result_counts() -> None:
    narrow = ProviderCache.key("OPENALEX", "SEARCH_CANDIDATES", "residual learning", "3")
    wide = ProviderCache.key("OPENALEX", "SEARCH_CANDIDATES", "residual learning", "10")

    assert narrow != wide


class _Limited(LLMClient):
    def __init__(self, retry_after: str | None, refusals: int = 1, **kwargs: object) -> None:
        super().__init__(api_key="stub", model="stub")
        self.retry_after = retry_after
        self.refusals = refusals
        self.attempts = 0

    def _send(self, messages, schema, timeout, temperature):
        self.attempts += 1
        if self.attempts <= self.refusals:
            raise LLMRateLimitedError("limited", retry_after_seconds=float(self.retry_after or 0.0))
        return json.dumps(
            {"verdict": "SUPPORTED", "evidence_span_ids": ["span_000"], "reason": "ok"}
        )


def test_a_rate_limit_is_waited_out_once_and_then_succeeds(monkeypatch: Any) -> None:
    slept: list[float] = []
    monkeypatch.setattr("app.providers.llm.time.sleep", slept.append)

    client = _Limited("0.5")
    prompt = Prompt(name="support", version="t", system="s", user="u")
    result = client.complete_structured(prompt, SupportResponse, remaining_seconds=30.0)

    assert result.value.verdict == "SUPPORTED"
    assert client.attempts == 2
    assert slept == [0.5]


def test_a_rate_limit_is_not_waited_out_beyond_the_budget(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.providers.llm.time.sleep", lambda _: None)

    client = _Limited("20")
    prompt = Prompt(name="support", version="t", system="s", user="u")

    with pytest.raises(LLMRateLimitedError):
        client.complete_structured(prompt, SupportResponse, remaining_seconds=2.0)

    assert client.attempts == 1


def test_a_rate_limit_without_a_retry_after_is_reported_not_slept_on() -> None:
    client = _Limited(None)
    prompt = Prompt(name="support", version="t", system="s", user="u")

    with pytest.raises(LLMRateLimitedError):
        client.complete_structured(prompt, SupportResponse, remaining_seconds=30.0)

    assert client.attempts == 1


def test_repeated_rate_limits_are_waited_out_within_the_cap(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.providers.llm.time.sleep", lambda _: None)

    client = _Limited("1", refusals=3)
    prompt = Prompt(name="support", version="t", system="s", user="u")
    result = client.complete_structured(prompt, SupportResponse, remaining_seconds=60.0)

    assert result.value.verdict == "SUPPORTED"
    assert client.attempts == 4


def test_a_provider_that_always_refuses_cannot_hold_the_operation_open(
    monkeypatch: Any,
) -> None:
    monkeypatch.setattr("app.providers.llm.time.sleep", lambda _: None)

    client = _Limited("1", refusals=99)
    prompt = Prompt(name="support", version="t", system="s", user="u")

    with pytest.raises(LLMRateLimitedError):
        client.complete_structured(prompt, SupportResponse, remaining_seconds=600.0)

    assert client.attempts == get_settings().llm_max_rate_limit_waits + 1


class _Always429:
    status_code = 429
    headers = {"Retry-After": "1"}
    text = "rate limited"

    @staticmethod
    def json() -> dict[str, Any]:
        return {}


def test_every_http_attempt_consumes_the_call_budget(monkeypatch: Any) -> None:
    posts: list[int] = []
    monkeypatch.setattr("app.providers.llm.time.sleep", lambda _: None)
    monkeypatch.setattr(get_settings(), "llm_max_calls_per_operation", 3)
    monkeypatch.setattr(
        "app.providers.llm.httpx.post",
        lambda *a, **k: (posts.append(1), _Always429())[1],
    )

    client = LLMClient(api_key="stub", model="stub")
    prompt = Prompt(name="support", version="t", system="s", user="u")

    with pytest.raises((LLMRateLimitedError, LLMUnavailableError)):
        client.complete_structured(prompt, SupportResponse, remaining_seconds=600.0)

    assert len(posts) == 3, f"budget of 3 allowed {len(posts)} requests"


def test_the_repair_attempt_also_consumes_budget(monkeypatch: Any) -> None:
    monkeypatch.setattr(get_settings(), "llm_max_calls_per_operation", 1)
    sent: list[int] = []

    class _Malformed:
        status_code = 200
        headers: dict[str, str] = {}
        text = ""

        @staticmethod
        def json() -> dict[str, Any]:
            sent.append(1)
            return {"choices": [{"message": {"content": json.dumps({"verdict": "SUPPORTED"})}}]}

    monkeypatch.setattr("app.providers.llm.httpx.post", lambda *a, **k: _Malformed())
    client = LLMClient(api_key="stub", model="stub")
    prompt = Prompt(name="support", version="t", system="s", user="u")

    with pytest.raises(LLMUnavailableError):
        client.complete_structured(prompt, SupportResponse, remaining_seconds=60.0)

    assert sent == [1], "the repair was sent without reserving budget"


def test_openai_luna_is_bounded_and_uses_no_reasoning_tokens(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class _Valid:
        status_code = 200
        headers: dict[str, str] = {}
        text = ""

        @staticmethod
        def json() -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "verdict": "SUPPORTED",
                                    "evidence_span_ids": ["span_000"],
                                    "reason": "The abstract says so.",
                                }
                            )
                        }
                    }
                ]
            }

    def post(*_args: Any, **kwargs: Any) -> _Valid:
        captured.update(kwargs["json"])
        return _Valid()

    monkeypatch.setattr("app.providers.llm.httpx.post", post)
    client = LLMClient(
        api_key="stub",
        model="gpt-5.6-luna",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )

    client.complete_structured(
        Prompt(name="support", version="t", system="s", user="u"), SupportResponse
    )

    assert captured["reasoning_effort"] == "none"
    assert captured["max_completion_tokens"] == get_settings().llm_max_output_tokens


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1512.03385", "1512.03385"),
        ("2005.14165v4", "2005.14165"),
        ("arXiv:1706.03762", "1706.03762"),
        ("math.GT/0309136", "math.GT/0309136"),
        ("hep-th/9901001", "hep-th/9901001"),
        ("10.1000/not-arxiv", None),
        ("", None),
        (None, None),
    ],
)
def test_arxiv_ids_are_normalised_to_the_form_their_doi_uses(
    raw: str | None, expected: str | None
) -> None:
    assert normalise_arxiv(raw) == expected


def test_an_arxiv_lookup_asks_for_the_registered_doi(monkeypatch: Any) -> None:
    asked: list[str] = []
    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", lambda url, **kw: (asked.append(url), _ARXIV_PAYLOAD)[1])

    work = client.get_by_arxiv("1512.03385v2")

    assert asked == ["https://api.openalex.org/works/doi:10.48550/arXiv.1512.03385"]
    assert work.title == "Deep Residual Learning for Image Recognition"


def test_a_malformed_arxiv_id_never_reaches_the_provider(monkeypatch: Any) -> None:
    client = OpenAlexClient()
    monkeypatch.setattr(
        client, "_get", lambda *a, **k: pytest.fail("a malformed id was sent upstream")
    )

    with pytest.raises(ProviderNotFoundError):
        client.get_by_arxiv("not-an-arxiv-id")


def test_an_absent_arxiv_work_is_a_miss_the_ladder_can_fall_through(
    monkeypatch: Any,
) -> None:
    client = OpenAlexClient()

    def missing(*args: Any, **kwargs: Any) -> Any:
        raise ProviderNotFoundError("no record", provider="OPENALEX")

    monkeypatch.setattr(client, "_get", missing)

    with pytest.raises(ProviderNotFoundError):
        client.get_by_arxiv("1706.03762")


@pytest.mark.parametrize(
    "client",
    [OpenAlexClient(), SemanticScholarClient()],
    ids=["openalex", "semantic-scholar"],
)
def test_a_record_with_no_identifier_is_refused_not_emptied(client: Any) -> None:
    with pytest.raises(ProviderInvalidResponseError):
        client.to_work({"unexpected": "shape"})


def test_a_doi_lookup_that_answers_with_another_work_is_refused(monkeypatch: Any) -> None:
    client = OpenAlexClient()
    monkeypatch.setattr(
        client,
        "_get",
        lambda *a, **k: {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.9/other"},
    )

    with pytest.raises(ProviderInvalidResponseError):
        client.get_by_doi("10.1/requested")


def test_an_arxiv_lookup_that_answers_with_another_preprint_is_refused(
    monkeypatch: Any,
) -> None:
    client = OpenAlexClient()
    monkeypatch.setattr(
        client,
        "_get",
        lambda *a, **k: {
            "id": "https://openalex.org/W1",
            "doi": "https://doi.org/10.48550/arXiv.2005.14165",
        },
    )

    with pytest.raises(ProviderInvalidResponseError):
        client.get_by_arxiv("1512.03385")


def test_the_requested_work_is_returned_unchanged(monkeypatch: Any) -> None:
    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", lambda *a, **k: _ARXIV_PAYLOAD)

    assert client.get_by_arxiv("1512.03385").external_id == "W2194775991"


def test_semantic_scholar_refuses_a_different_doi(monkeypatch: Any) -> None:
    client = SemanticScholarClient()
    monkeypatch.setattr(
        client,
        "_get",
        lambda *a, **k: {
            "paperId": "s2-other",
            "externalIds": {"DOI": "10.9/other"},
            "title": "Another work",
        },
    )

    with pytest.raises(ProviderInvalidResponseError):
        client.get_by_doi("10.1/requested")


def test_semantic_scholar_normalises_and_checks_arxiv_ids(monkeypatch: Any) -> None:
    asked: list[str] = []
    client = SemanticScholarClient()
    monkeypatch.setattr(
        client,
        "_get",
        lambda url, **kw: (
            asked.append(url),
            {
                "paperId": "s2-other",
                "externalIds": {"ArXiv": "2005.14165"},
                "title": "Another preprint",
            },
        )[1],
    )

    with pytest.raises(ProviderInvalidResponseError):
        client.get_by_arxiv("1512.03385v2")
    assert asked == ["https://api.semanticscholar.org/graph/v1/paper/arXiv:1512.03385"]


_ARXIV_PAYLOAD: dict[str, Any] = {
    "id": "https://openalex.org/W2194775991",
    "doi": "https://doi.org/10.48550/arxiv.1512.03385",
    "title": "Deep Residual Learning for Image Recognition",
    "publication_year": 2015,
    "authorships": [{"author": {"display_name": "Kaiming He"}}],
}


def test_a_budget_exhaustion_is_not_re_reported_as_a_provider_outage() -> None:
    session = ProviderSession(operation_id="op_test")
    session.degrade(
        ProviderName.OPENALEX,
        ProviderBudgetExhaustedError("budget spent", provider="OPENALEX"),
    )

    with pytest.raises(ProviderBudgetExhaustedError):
        session.claim_call(ProviderName.OPENALEX)


def test_a_rate_limited_provider_still_reports_a_rate_limit() -> None:
    session = ProviderSession(operation_id="op_test")
    session.degrade(
        ProviderName.SEMANTIC_SCHOLAR,
        ProviderRateLimitedError("429", provider="SEMANTIC_SCHOLAR"),
    )

    with pytest.raises(ProviderRateLimitedError):
        session.claim_call(ProviderName.SEMANTIC_SCHOLAR)


def test_a_retried_request_costs_the_operation_two_calls(monkeypatch: Any) -> None:
    monkeypatch.setattr("app.providers.http.time.sleep", lambda _: None)
    session = ProviderSession(operation_id="op_test")

    sent: list[int] = []

    class _Limited:
        status_code = 429
        headers = {"Retry-After": "0"}
        text = ""

        @staticmethod
        def json() -> dict[str, Any]:
            return {}

    monkeypatch.setattr("app.providers.http._send", lambda *a, **k: (sent.append(1), _Limited())[1])

    client = SemanticScholarClient(
        on_attempt=session.attempt_recorder(ProviderName.SEMANTIC_SCHOLAR)
    )
    session.claim_call(ProviderName.SEMANTIC_SCHOLAR)

    with pytest.raises(ProviderRateLimitedError):
        client.search("anything", limit=3)

    assert len(sent) == 2, "the transport did not retry"
    assert session.attempts_made[ProviderName.SEMANTIC_SCHOLAR] == 2
    assert session.total_calls == 2, "the operation counted one call for two requests"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        (
            "Can active memory replace attention?",
            "Can active memory replace attention",
        ),
        ("Learning * representations", "Learning representations"),
        ("What is a transformer? A survey", "What is a transformer A survey"),
        ('The "attention" mechanism', 'The "attention" mechanism'),
        ("Self-supervised learning", "Self-supervised learning"),
        ("Speech & language processing", "Speech & language processing"),
        ("A method [revised]", "A method [revised]"),
    ],
)
def test_only_the_syntax_openalex_refuses_is_stripped(title: str, expected: str) -> None:
    assert _filter_safe(title) == expected


def test_a_rejected_query_does_not_say_the_provider_is_unhealthy() -> None:
    assert not ProviderQueryRejectedError("malformed", provider="OPENALEX").degrades_provider
    assert ProviderRateLimitedError("429", provider="OPENALEX").degrades_provider
    assert ProviderUnavailableError("500", provider="OPENALEX").degrades_provider
    assert ProviderInvalidResponseError("401", provider="OPENALEX").degrades_provider


def test_one_rejected_query_does_not_end_the_provider_s_coverage(monkeypatch: Any) -> None:

    def service_raising(error: Exception) -> tuple[AcademicRetrievalService, ProviderSession]:
        session = ProviderSession(operation_id="op_test")
        service = AcademicRetrievalService(session)

        def fail(*args: Any, **kwargs: Any) -> list[ProviderWork]:
            raise error

        monkeypatch.setattr(service.openalex, "search", fail)
        monkeypatch.setattr(service.semantic_scholar, "search", fail)
        return service, session

    rejected, rejected_session = service_raising(
        ProviderQueryRejectedError("400", provider="OPENALEX")
    )
    rejected.search("a malformed title", Deadline.after("test", 30.0))
    assert not rejected_session.is_degraded(ProviderName.OPENALEX)

    limited, limited_session = service_raising(ProviderRateLimitedError("429", provider="OPENALEX"))
    limited.search("anything", Deadline.after("test", 30.0))
    assert limited_session.is_degraded(ProviderName.OPENALEX)
