from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.domain.errors import ProviderInvalidResponseError, ProviderNotFoundError
from app.domain.source import ProviderName, ProviderWork
from app.providers.http import request_json
from app.providers.openalex import normalise_arxiv, normalise_doi
from app.settings import get_settings

BASE_URL = "https://api.semanticscholar.org/graph/v1"

FIELDS = "paperId,externalIds,url,title,abstract,year,venue,authors,publicationTypes"

_CSL_TYPE: dict[str, str] = {
    "JournalArticle": "article-journal",
    "Conference": "paper-conference",
    "Book": "book",
    "BookSection": "chapter",
    "Review": "article-journal",
    "Dataset": "dataset",
}


class SemanticScholarClient:
    provider = ProviderName.SEMANTIC_SCHOLAR

    def __init__(
        self,
        timeout: float | None = None,
        on_attempt: Callable[[], None] | None = None,
    ) -> None:
        self.on_attempt = on_attempt
        settings = get_settings()
        self.timeout = timeout if timeout is not None else settings.provider_timeout_seconds
        self.api_key = settings.semantic_scholar_api_key

    def get_by_doi(self, doi: str, *, timeout: float | None = None) -> ProviderWork:
        normalised = normalise_doi(doi)
        if not normalised:
            raise ProviderNotFoundError("Not a DOI.", provider=self.provider.value)
        work = self.to_work(self._get(f"{BASE_URL}/paper/DOI:{normalised}", timeout=timeout))
        return _matching(work, doi=normalised)

    def get_by_arxiv(self, arxiv_id: str, *, timeout: float | None = None) -> ProviderWork:
        normalised = normalise_arxiv(arxiv_id)
        if not normalised:
            raise ProviderNotFoundError("Not an arXiv id.", provider=self.provider.value)
        work = self.to_work(self._get(f"{BASE_URL}/paper/arXiv:{normalised}", timeout=timeout))
        return _matching(work, arxiv_id=normalised)

    def search(self, query: str, *, limit: int, timeout: float | None = None) -> list[ProviderWork]:
        payload = self._get(
            f"{BASE_URL}/paper/search",
            params={"query": query, "limit": max(1, min(limit, 20))},
            timeout=timeout,
            retry=True,
        )
        data = payload.get("data")
        if not isinstance(data, list):
            return []
        return [self.to_work(item) for item in data[:limit] if isinstance(item, dict)]

    def search_by_title(
        self, title: str, *, limit: int, timeout: float | None = None
    ) -> list[ProviderWork]:
        return self.search(title, limit=limit, timeout=timeout)

    def _get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None,
        retry: bool = False,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = {"fields": FIELDS}
        merged.update(params or {})
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        return request_json(
            "GET",
            url,
            provider=self.provider.value,
            params=merged,
            headers=headers,
            timeout=timeout if timeout is not None else self.timeout,
            retry_on_rate_limit=retry,
            min_interval_seconds=get_settings().semantic_scholar_min_interval_seconds,
            on_attempt=self.on_attempt,
        )

    @classmethod
    def to_work(cls, payload: dict[str, Any]) -> ProviderWork:
        paper_id = str(payload.get("paperId") or "")
        if not paper_id:
            raise ProviderInvalidResponseError(
                "The provider returned a record with no identifier.",
                provider=cls.provider.value,
                keys=sorted(payload)[:10],
            )

        external = payload.get("externalIds") or {}
        types = payload.get("publicationTypes") or []
        work_type = next(
            (_CSL_TYPE[kind] for kind in types if isinstance(kind, str) and kind in _CSL_TYPE),
            "document",
        )
        abstract = payload.get("abstract")
        return ProviderWork(
            provider=cls.provider,
            external_id=str(payload.get("paperId") or ""),
            title=str(payload.get("title") or "").strip(),
            doi=normalise_doi(str(external.get("DOI") or "")),
            arxiv_id=_arxiv(external),
            year=payload.get("year") if isinstance(payload.get("year"), int) else None,
            authors=tuple(
                str(entry.get("name") or "").strip()
                for entry in payload.get("authors") or []
                if isinstance(entry, dict) and entry.get("name")
            ),
            venue=str(payload.get("venue") or "").strip() or None,
            abstract=abstract.strip() if isinstance(abstract, str) and abstract.strip() else None,
            url=str(payload.get("url") or "") or None,
            work_type=work_type,
            raw=payload,
        )


def _arxiv(external: dict[str, Any]) -> str | None:
    value = external.get("ArXiv")
    if isinstance(value, str) and re.fullmatch(r"[\w.\-/]+", value):
        return value
    return None


def _matching(work: ProviderWork, *, doi: str = "", arxiv_id: str = "") -> ProviderWork:
    if doi and work.doi and normalise_doi(work.doi) != doi:
        raise ProviderInvalidResponseError(
            "The provider returned a different work from the one requested.",
            provider=work.provider.value,
            requested=doi,
            returned=work.doi,
        )
    if arxiv_id and work.arxiv_id:
        returned = normalise_arxiv(work.arxiv_id)
        if returned and returned != arxiv_id:
            raise ProviderInvalidResponseError(
                "The provider returned a different preprint from the one requested.",
                provider=work.provider.value,
                requested=arxiv_id,
                returned=returned,
            )
    return work
