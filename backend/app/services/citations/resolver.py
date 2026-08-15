from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar, cast

from app.domain.errors import ProviderError, ProviderNotFoundError
from app.domain.reference import ReferenceRecord, ResolutionConfidence, ResolutionMethod
from app.domain.source import ProviderName, ProviderWork, RetrievalPurpose
from app.providers.cache import get_cache
from app.providers.openalex import OpenAlexClient, normalise_doi
from app.providers.semantic_scholar import SemanticScholarClient
from app.providers.session import AttemptRecord, ProviderSession
from app.services.deadline import Deadline
from app.settings import get_settings

TITLE_MATCH_THRESHOLD = 0.72

_STOPWORDS = frozenset(
    {"a", "an", "and", "for", "from", "in", "of", "on", "the", "to", "via", "with"}
)

T = TypeVar("T")


ProviderClient = OpenAlexClient | SemanticScholarClient


@dataclass(frozen=True)
class Resolution:
    method: ResolutionMethod
    confidence: ResolutionConfidence
    work: ProviderWork | None = None

    @property
    def resolved(self) -> bool:
        return self.work is not None and self.confidence in (
            ResolutionConfidence.CERTAIN,
            ResolutionConfidence.PROBABLE,
        )

    @classmethod
    def unresolved(cls) -> Resolution:
        return cls(method=ResolutionMethod.UNRESOLVED, confidence=ResolutionConfidence.NONE)


class ReferenceResolver:
    def __init__(
        self,
        session: ProviderSession,
        *,
        openalex: OpenAlexClient | None = None,
        semantic_scholar: SemanticScholarClient | None = None,
    ) -> None:
        self.session = session
        self.openalex = openalex or OpenAlexClient(
            on_attempt=session.attempt_recorder(ProviderName.OPENALEX)
        )
        self.semantic_scholar = semantic_scholar or SemanticScholarClient(
            on_attempt=session.attempt_recorder(ProviderName.SEMANTIC_SCHOLAR)
        )

    def resolve(self, reference: ReferenceRecord, deadline: Deadline) -> Resolution:
        doi = normalise_doi(reference.csl.DOI) or doi_in(reference.raw_text)
        if doi:
            work = self._by_identifier("doi", doi, deadline)
            if work is not None:
                return Resolution(ResolutionMethod.DOI, ResolutionConfidence.CERTAIN, work)

        arxiv = arxiv_in(reference.raw_text)
        if arxiv:
            work = self._by_identifier("arxiv", arxiv, deadline)
            if work is not None:
                return Resolution(ResolutionMethod.ARXIV, ResolutionConfidence.CERTAIN, work)

        title = (reference.csl.title or "").strip()
        if not title:
            return Resolution.unresolved()
        return self._by_metadata(title, reference, deadline)

    def _by_identifier(self, kind: str, value: str, deadline: Deadline) -> ProviderWork | None:
        for provider in (ProviderName.OPENALEX, ProviderName.SEMANTIC_SCHOLAR):
            work = self._fetch_one(provider, kind, value, deadline)
            if work is not None:
                return work
        return None

    def _fetch_one(
        self, provider: ProviderName, kind: str, value: str, deadline: Deadline
    ) -> ProviderWork | None:
        def action(client: ProviderClient, timeout: float) -> ProviderWork:
            if kind == "doi":
                return client.get_by_doi(value, timeout=timeout)
            return client.get_by_arxiv(value, timeout=timeout)

        return self._call(
            provider,
            RetrievalPurpose.RESOLVE_REFERENCE,
            f"{kind}:{value}",
            deadline,
            action,
        )

    def _by_metadata(
        self, title: str, reference: ReferenceRecord, deadline: Deadline
    ) -> Resolution:
        limit = get_settings().max_resolution_candidates

        def action(client: ProviderClient, timeout: float) -> list[ProviderWork]:
            return client.search_by_title(title, limit=limit, timeout=timeout)

        candidates: list[ProviderWork] = []
        for provider in (ProviderName.OPENALEX, ProviderName.SEMANTIC_SCHOLAR):
            found = self._call(
                provider, RetrievalPurpose.RESOLVE_REFERENCE, f"title:{title}", deadline, action
            )
            if found:
                candidates.extend(found)

        best = best_title_match(title, candidates)
        if best is None:
            return Resolution.unresolved()

        year = reference.csl.year
        corroborated = (year is not None and best.year == year) or _shares_surname(
            best, _surnames(reference)
        )
        if corroborated:
            return Resolution(
                ResolutionMethod.TITLE_AUTHOR_YEAR, ResolutionConfidence.PROBABLE, best
            )
        return Resolution(ResolutionMethod.TITLE_ONLY, ResolutionConfidence.UNCERTAIN, best)

    def fetch_abstract(self, work: ProviderWork, deadline: Deadline) -> ProviderWork:
        if work.has_abstract or not work.doi:
            return work
        found = self._fetch_one(ProviderName.SEMANTIC_SCHOLAR, "doi", work.doi, deadline)
        if found is not None and found.has_abstract:
            return work.model_copy(update={"abstract": found.abstract})
        return work

    def client_for(self, provider: ProviderName) -> ProviderClient:
        return self.openalex if provider is ProviderName.OPENALEX else self.semantic_scholar

    def _call(
        self,
        provider: ProviderName,
        purpose: RetrievalPurpose,
        query: str,
        deadline: Deadline,
        action: Callable[[ProviderClient, float], T],
    ) -> T | None:
        if self.session.is_degraded(provider) or deadline.expired:
            return None

        cache = get_cache()
        key = cache.key(provider.value, purpose.value, query)
        cached = cache.get(key)
        if cached is not None:
            return None if isinstance(cached, _Missing) else cast(T, cached)

        started = time.monotonic()
        try:
            self.session.claim_call(provider)
            result = action(self.client_for(provider), deadline.budget_for(_provider_timeout()))
        except ProviderNotFoundError:
            cache.put(key, _MISSING, empty=True)
            self._record(provider, purpose, query, "NOT_FOUND", None, count=0, started=started)
            return None
        except ProviderError as exc:
            if exc.degrades_provider:
                self.session.degrade(provider, exc)
            code = exc.code.value
            self._record(provider, purpose, query, "FAILED", code, count=0, started=started)
            return None

        count = len(result) if isinstance(result, list) else 1
        cache.put(key, result, empty=count == 0)
        self._record(provider, purpose, query, "OK", None, count=count, started=started)
        return result

    def _record(
        self,
        provider: ProviderName,
        purpose: RetrievalPurpose,
        query: str,
        status: str,
        failure_code: str | None,
        *,
        count: int,
        started: float,
    ) -> None:
        self.session.record(
            AttemptRecord(
                provider=provider,
                purpose=purpose,
                query=query,
                status=status,
                failure_code=failure_code,
                result_count=count,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        )


class _Missing:
    pass


_MISSING = _Missing()


def _provider_timeout() -> float:
    return get_settings().provider_timeout_seconds


def title_key(title: str) -> frozenset[str]:
    folded = unicodedata.normalize("NFKD", title).casefold()
    return frozenset(word for word in re.findall(r"[a-z0-9]+", folded) if word not in _STOPWORDS)


def title_similarity(left: str, right: str) -> float:
    a, b = title_key(left), title_key(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def best_title_match(title: str, candidates: list[ProviderWork]) -> ProviderWork | None:
    scored = [
        (title_similarity(title, candidate.title), candidate)
        for candidate in candidates
        if candidate.title
    ]
    viable = [pair for pair in scored if pair[0] >= TITLE_MATCH_THRESHOLD]
    if not viable:
        return None
    return max(viable, key=lambda pair: pair[0])[1]


def _surnames(reference: ReferenceRecord) -> set[str]:
    names: set[str] = set()
    for author in reference.csl.author:
        family = str(author.get("family") or "").strip()
        literal = str(author.get("literal") or "").strip()
        if family:
            names.add(family.casefold())
        elif literal:
            names.add(literal.split()[-1].casefold())
    return names


def _shares_surname(work: ProviderWork, surnames: set[str]) -> bool:
    if not surnames:
        return False
    found = {name.split()[-1].casefold() for name in work.authors if name.strip()}
    return bool(found & surnames)


def doi_in(text: str) -> str | None:
    match = re.search(r"\b10\.\d{4,9}/[^\s\"<>]+", text)
    return normalise_doi(match.group(0)) if match else None


def arxiv_in(text: str) -> str | None:
    match = re.search(r"arxiv[:\s/]*(\d{4}\.\d{4,5}(v\d+)?)", text, flags=re.IGNORECASE)
    return match.group(1) if match else None
