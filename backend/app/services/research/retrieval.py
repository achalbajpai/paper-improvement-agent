from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.domain.errors import ProviderError
from app.domain.reference import CSLItem
from app.domain.review import ProviderDegradation
from app.domain.source import ProviderName, ProviderWork, RetrievalPurpose
from app.providers.cache import get_cache
from app.providers.openalex import OpenAlexClient
from app.providers.semantic_scholar import SemanticScholarClient
from app.providers.session import AttemptRecord, ProviderSession
from app.services.citations.resolver import TITLE_MATCH_THRESHOLD, title_similarity
from app.services.deadline import Deadline
from app.settings import get_settings


@dataclass(frozen=True)
class Candidate:
    id: str
    work: ProviderWork

    csl: CSLItem

    def summary(self) -> str:
        authors = ", ".join(self.work.authors[:3]) or "unknown authors"
        year = self.work.year or "n.d."
        parts = [f"{self.work.title} ({authors}, {year})"]
        if self.work.venue:
            parts.append(self.work.venue)
        if self.work.abstract:
            parts.append(self.work.abstract[:600])
        return "\n".join(parts)


@dataclass
class VerifiedCandidateSet:
    query: str
    candidates: list[Candidate] = field(default_factory=list)
    degradations: tuple[ProviderDegradation, ...] = ()

    rejected_incomplete: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.candidates

    def by_id(self) -> dict[str, Candidate]:
        return {candidate.id: candidate for candidate in self.candidates}


class AcademicRetrievalService:
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

    def search(
        self, query: str, deadline: Deadline, *, limit: int | None = None
    ) -> VerifiedCandidateSet:
        settings = get_settings()
        keep = limit or settings.max_candidates_per_query
        result = VerifiedCandidateSet(query=query)

        works: list[ProviderWork] = []
        for provider in (ProviderName.OPENALEX, ProviderName.SEMANTIC_SCHOLAR):
            found = self._search_one(provider, query, keep, deadline)
            works.extend(found)

        result.degradations = self.session.degradations
        for work in _deduplicate_works(works):
            csl = work.to_csl(f"cand_{len(result.candidates):03d}")
            if not csl.is_complete_for_insertion():
                result.rejected_incomplete += 1
                continue
            result.candidates.append(
                Candidate(id=f"cand_{len(result.candidates):03d}", work=work, csl=csl)
            )
            if len(result.candidates) >= keep:
                break
        return result

    def _search_one(
        self, provider: ProviderName, query: str, limit: int, deadline: Deadline
    ) -> list[ProviderWork]:
        if self.session.is_degraded(provider) or deadline.expired:
            return []

        cache = get_cache()
        key = cache.key(provider.value, RetrievalPurpose.SEARCH_CANDIDATES.value, query, str(limit))
        cached = cache.get(key)
        if cached is not None:
            return list(cached)

        client: OpenAlexClient | SemanticScholarClient = (
            self.openalex if provider is ProviderName.OPENALEX else self.semantic_scholar
        )
        started = time.monotonic()
        try:
            self.session.claim_call(provider)
            works = client.search(
                query, limit=limit, timeout=deadline.budget_for(_provider_timeout())
            )
        except ProviderError as exc:
            if exc.degrades_provider:
                self.session.degrade(provider, exc)
            self._record(provider, query, "FAILED", exc.code.value, count=0, started=started)
            return []

        cache.put(key, works, empty=not works)
        self._record(provider, query, "OK", None, count=len(works), started=started)
        return works

    def _record(
        self,
        provider: ProviderName,
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
                purpose=RetrievalPurpose.SEARCH_CANDIDATES,
                query=query,
                status=status,
                failure_code=failure_code,
                result_count=count,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        )


def _provider_timeout() -> float:
    return get_settings().provider_timeout_seconds


def _deduplicate_works(works: list[ProviderWork]) -> list[ProviderWork]:
    accepted: list[ProviderWork] = []
    seen_keys: set[str] = set()
    for work in works:
        keys = set(work.identity_keys())
        same_title = bool(work.title) and any(
            existing.title and title_similarity(work.title, existing.title) >= TITLE_MATCH_THRESHOLD
            for existing in accepted
        )
        if keys & seen_keys or same_title:
            continue
        accepted.append(work)
        seen_keys.update(keys)
    return accepted
