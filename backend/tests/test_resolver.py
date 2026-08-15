from __future__ import annotations

import pytest

from app.domain.errors import ProviderNotFoundError, ProviderRateLimitedError
from app.domain.reference import (
    CSLItem,
    ReferenceRecord,
    ResolutionConfidence,
    ResolutionMethod,
)
from app.domain.source import ProviderName, ProviderWork
from app.providers.cache import get_cache
from app.providers.openalex import OpenAlexClient
from app.providers.semantic_scholar import SemanticScholarClient
from app.providers.session import ProviderSession
from app.services.citations.resolver import (
    ReferenceResolver,
    title_similarity,
)
from app.services.deadline import Deadline

TITLE = "Optuna: A Next-generation Hyperparameter Optimization Framework"


def work(
    *,
    provider: ProviderName = ProviderName.OPENALEX,
    title: str = TITLE,
    year: int | None = 2019,
    authors: tuple[str, ...] = ("Takuya Akiba",),
    doi: str | None = "10.1145/3292500.3330701",
    abstract: str | None = None,
) -> ProviderWork:
    return ProviderWork(
        provider=provider,
        external_id="W1",
        title=title,
        year=year,
        authors=authors,
        doi=doi,
        abstract=abstract,
    )


def reference(
    *,
    title: str | None = TITLE,
    year: int | None = 2019,
    surname: str | None = "Akiba",
    doi: str | None = None,
    raw_text: str = "",
) -> ReferenceRecord:
    fields: dict[str, object] = {"id": "ref_imported_001", "title": title}
    if year is not None:
        fields["issued"] = {"date-parts": [[year]]}
    if surname:
        fields["author"] = [{"family": surname, "given": "T."}]
    if doi:
        fields["DOI"] = doi
    return ReferenceRecord(
        id="ref_imported_001", csl=CSLItem.model_validate(fields), raw_text=raw_text
    )


class FakeOpenAlex(OpenAlexClient):
    def __init__(
        self,
        *,
        by_doi: ProviderWork | None = None,
        results: list[ProviderWork] | None = None,
    ) -> None:
        super().__init__()
        self._by_doi = by_doi
        self._results = results or []
        self.calls: list[str] = []

    def get_by_doi(self, doi: str, *, timeout: float | None = None) -> ProviderWork:
        self.calls.append(f"doi:{doi}")
        if self._by_doi is None:
            raise ProviderNotFoundError("no record", provider="OPENALEX")
        return self._by_doi

    def get_by_arxiv(self, arxiv_id: str, *, timeout: float | None = None) -> ProviderWork:
        self.calls.append(f"arxiv:{arxiv_id}")
        if self._by_doi is None:
            raise ProviderNotFoundError("no record", provider="OPENALEX")
        return self._by_doi

    def search_by_title(
        self, title: str, *, limit: int, timeout: float | None = None
    ) -> list[ProviderWork]:
        self.calls.append(f"title:{title}")
        return self._results


class FakeS2(SemanticScholarClient):
    def __init__(
        self,
        *,
        by_doi: ProviderWork | None = None,
        results: list[ProviderWork] | None = None,
        rate_limited: bool = False,
    ) -> None:
        super().__init__()
        self._by_doi = by_doi
        self._results = results or []
        self._rate_limited = rate_limited
        self.calls: list[str] = []

    def get_by_doi(self, doi: str, *, timeout: float | None = None) -> ProviderWork:
        self.calls.append(f"doi:{doi}")
        if self._rate_limited:
            raise ProviderRateLimitedError("limited", provider="SEMANTIC_SCHOLAR")
        if self._by_doi is None:
            raise ProviderNotFoundError("no record", provider="SEMANTIC_SCHOLAR")
        return self._by_doi

    def get_by_arxiv(self, arxiv_id: str, *, timeout: float | None = None) -> ProviderWork:
        self.calls.append(f"arxiv:{arxiv_id}")
        if self._rate_limited:
            raise ProviderRateLimitedError("limited", provider="SEMANTIC_SCHOLAR")
        if self._by_doi is None:
            raise ProviderNotFoundError("no record", provider="SEMANTIC_SCHOLAR")
        return self._by_doi

    def search_by_title(
        self, title: str, *, limit: int, timeout: float | None = None
    ) -> list[ProviderWork]:
        self.calls.append(f"title:{title}")
        if self._rate_limited:
            raise ProviderRateLimitedError("limited", provider="SEMANTIC_SCHOLAR")
        return self._results


@pytest.fixture(autouse=True)
def clean_cache() -> None:
    get_cache().clear()


def build(openalex: FakeOpenAlex, s2: FakeS2) -> tuple[ReferenceResolver, Deadline]:
    session = ProviderSession(operation_id="op_test")
    resolver = ReferenceResolver(session, openalex=openalex, semantic_scholar=s2)
    return resolver, Deadline.after("test", 30.0)


def test_a_doi_match_is_certain() -> None:
    openalex = FakeOpenAlex(by_doi=work())
    resolver, deadline = build(openalex, FakeS2())

    result = resolver.resolve(reference(doi="10.1145/3292500.3330701"), deadline)

    assert result.method is ResolutionMethod.DOI
    assert result.confidence is ResolutionConfidence.CERTAIN
    assert result.resolved


def test_a_doi_found_in_the_raw_string_is_used_when_grobid_missed_it() -> None:
    openalex = FakeOpenAlex(by_doi=work())
    resolver, deadline = build(openalex, FakeS2())

    result = resolver.resolve(
        reference(doi=None, raw_text="Akiba et al. 2019. doi:10.1145/3292500.3330701"), deadline
    )

    assert result.confidence is ResolutionConfidence.CERTAIN


def test_the_ladder_stops_at_the_first_rung_that_matches() -> None:
    openalex = FakeOpenAlex(by_doi=work())
    resolver, deadline = build(openalex, FakeS2())

    resolver.resolve(reference(doi="10.1145/3292500.3330701"), deadline)

    assert openalex.calls == ["doi:10.1145/3292500.3330701"]


def test_an_arxiv_id_in_the_raw_string_resolves_as_certain() -> None:
    openalex = FakeOpenAlex(by_doi=work(doi=None))
    resolver, deadline = build(openalex, FakeS2())

    result = resolver.resolve(reference(raw_text="Akiba et al. arXiv:1907.10902"), deadline)

    assert result.method is ResolutionMethod.ARXIV
    assert result.confidence is ResolutionConfidence.CERTAIN


def test_a_title_match_corroborated_by_the_year_is_probable() -> None:
    openalex = FakeOpenAlex(results=[work(authors=("Someone Else",))])
    resolver, deadline = build(openalex, FakeS2())

    result = resolver.resolve(reference(surname=None), deadline)

    assert result.method is ResolutionMethod.TITLE_AUTHOR_YEAR
    assert result.confidence is ResolutionConfidence.PROBABLE
    assert result.resolved


def test_a_title_match_corroborated_by_a_surname_is_probable() -> None:
    openalex = FakeOpenAlex(results=[work(year=2020)])
    resolver, deadline = build(openalex, FakeS2())

    result = resolver.resolve(reference(year=2019), deadline)

    assert result.confidence is ResolutionConfidence.PROBABLE


def test_an_uncorroborated_title_match_is_uncertain_and_not_resolved() -> None:
    openalex = FakeOpenAlex(results=[work(year=1998, authors=("Someone Else",))])
    resolver, deadline = build(openalex, FakeS2())

    result = resolver.resolve(reference(year=2019, surname="Akiba"), deadline)

    assert result.method is ResolutionMethod.TITLE_ONLY
    assert result.confidence is ResolutionConfidence.UNCERTAIN
    assert not result.resolved


def test_a_different_paper_is_no_match_however_confident_the_search_looked() -> None:
    openalex = FakeOpenAlex(results=[work(title="Deep Residual Learning for Image Recognition")])
    resolver, deadline = build(openalex, FakeS2())

    result = resolver.resolve(reference(), deadline)

    assert result.method is ResolutionMethod.UNRESOLVED
    assert result.work is None


def test_metadata_resolution_consults_both_corpora_before_concluding() -> None:
    unrelated = work(title="Deep Residual Learning for Image Recognition")
    semantic_match = work(provider=ProviderName.SEMANTIC_SCHOLAR)
    openalex = FakeOpenAlex(results=[unrelated])
    s2 = FakeS2(results=[semantic_match])
    resolver, deadline = build(openalex, s2)

    result = resolver.resolve(reference(), deadline)

    assert result.confidence is ResolutionConfidence.PROBABLE
    assert result.work == semantic_match
    assert s2.calls == [f"title:{TITLE}"]


def test_a_reference_with_no_title_is_unresolved_without_calling_anyone() -> None:
    openalex = FakeOpenAlex()
    resolver, deadline = build(openalex, FakeS2())

    result = resolver.resolve(reference(title=None, doi=None), deadline)

    assert result.method is ResolutionMethod.UNRESOLVED
    assert openalex.calls == []


def test_title_similarity_ignores_stopwords_and_case() -> None:
    assert title_similarity("Attention Is All You Need", "attention is all you need") == 1.0
    assert title_similarity("A Study of Cats", "The Study of Cats") == 1.0
    assert title_similarity("Attention Is All You Need", "Deep Residual Learning") == 0.0


def test_a_rate_limited_provider_is_skipped_for_the_rest_of_the_operation() -> None:
    s2 = FakeS2(rate_limited=True)
    openalex = FakeOpenAlex()
    resolver, deadline = build(openalex, s2)

    resolver.resolve(reference(doi="10.1145/3292500.3330701"), deadline)
    resolver.resolve(
        reference(doi="10.1145/3292500.3330702").model_copy(update={"id": "ref_imported_001"}),
        deadline,
    )

    assert resolver.session.is_degraded(ProviderName.SEMANTIC_SCHOLAR)
    assert len(s2.calls) == 1


def test_degradation_is_reported_rather_than_swallowed() -> None:
    resolver, deadline = build(FakeOpenAlex(), FakeS2(rate_limited=True))
    resolver.resolve(reference(doi="10.1145/3292500.3330701"), deadline)

    codes = {degradation.code for degradation in resolver.session.degradations}
    assert codes == {"PROVIDER_RATE_LIMITED"}


def test_a_transient_failure_is_never_cached_but_a_genuine_miss_is() -> None:
    s2 = FakeS2(rate_limited=True)
    resolver, deadline = build(FakeOpenAlex(), s2)
    resolver.resolve(reference(doi="10.1145/3292500.3330701"), deadline)

    cache = get_cache()
    query = "doi:10.1145/3292500.3330701"
    assert cache.get(cache.key("SEMANTIC_SCHOLAR", "RESOLVE_REFERENCE", query)) is None

    assert cache.get(cache.key("OPENALEX", "RESOLVE_REFERENCE", query)) is not None


def test_a_cached_miss_does_not_call_the_provider_again() -> None:
    openalex = FakeOpenAlex()
    resolver, deadline = build(openalex, FakeS2())

    resolver.resolve(reference(doi="10.1145/3292500.3330701"), deadline)
    resolver.resolve(reference(doi="10.1145/3292500.3330701"), deadline)

    assert openalex.calls.count("doi:10.1145/3292500.3330701") == 1


def test_an_abstract_is_fetched_from_semantic_scholar_when_openalex_had_none() -> None:
    s2 = FakeS2(by_doi=work(provider=ProviderName.SEMANTIC_SCHOLAR, abstract="We propose Optuna."))
    resolver, deadline = build(FakeOpenAlex(), s2)

    enriched = resolver.fetch_abstract(work(abstract=None), deadline)

    assert enriched.abstract == "We propose Optuna."
    assert enriched.provider is ProviderName.OPENALEX


def test_an_existing_abstract_is_never_refetched() -> None:
    s2 = FakeS2(by_doi=work(abstract="different text"))
    resolver, deadline = build(FakeOpenAlex(), s2)

    unchanged = resolver.fetch_abstract(work(abstract="the original"), deadline)

    assert unchanged.abstract == "the original"
    assert s2.calls == []
