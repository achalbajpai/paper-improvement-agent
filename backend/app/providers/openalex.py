from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.domain.errors import ProviderInvalidResponseError, ProviderNotFoundError
from app.domain.source import ProviderName, ProviderWork
from app.providers.http import request_json
from app.settings import get_settings

BASE_URL = "https://api.openalex.org"

_MAX_INVERTED_POSITIONS = 40_000


_CSL_TYPE: dict[str, str] = {
    "article": "article-journal",
    "journal-article": "article-journal",
    "book": "book",
    "book-chapter": "chapter",
    "conference-paper": "paper-conference",
    "proceedings-article": "paper-conference",
    "dissertation": "thesis",
    "preprint": "article",
    "report": "report",
    "dataset": "dataset",
}


class OpenAlexClient:
    provider = ProviderName.OPENALEX

    def __init__(
        self,
        timeout: float | None = None,
        on_attempt: Callable[[], None] | None = None,
    ) -> None:
        self.on_attempt = on_attempt
        settings = get_settings()
        self.timeout = timeout if timeout is not None else settings.provider_timeout_seconds
        self.api_key = settings.openalex_api_key
        self.mailto = settings.openalex_mailto

    def get_by_doi(self, doi: str, *, timeout: float | None = None) -> ProviderWork:
        normalised = normalise_doi(doi)
        if not normalised:
            raise ProviderNotFoundError("Not a DOI.", provider=self.provider.value)
        payload = self._get(f"{BASE_URL}/works/doi:{normalised}", timeout=timeout)
        return _matching(self.to_work(payload), doi=normalised)

    def get_by_arxiv(self, arxiv_id: str, *, timeout: float | None = None) -> ProviderWork:
        normalised = normalise_arxiv(arxiv_id)
        if not normalised:
            raise ProviderNotFoundError("Not an arXiv id.", provider=self.provider.value)

        payload = self._get(f"{BASE_URL}/works/doi:{ARXIV_DOI_PREFIX}{normalised}", timeout=timeout)
        return _matching(self.to_work(payload), arxiv_id=normalised)

    def search(self, query: str, *, limit: int, timeout: float | None = None) -> list[ProviderWork]:
        payload = self._get(
            f"{BASE_URL}/works",
            params={"search": query, "per_page": max(1, min(limit, 50))},
            timeout=timeout,
        )
        return [self.to_work(item) for item in _results(payload)[:limit]]

    def search_by_title(
        self, title: str, *, limit: int, timeout: float | None = None
    ) -> list[ProviderWork]:
        payload = self._get(
            f"{BASE_URL}/works",
            params={
                "filter": f"title.search:{_filter_safe(title)}",
                "per_page": max(1, min(limit, 25)),
            },
            timeout=timeout,
        )
        return [self.to_work(item) for item in _results(payload)[:limit]]

    def _get(
        self, url: str, *, params: dict[str, Any] | None = None, timeout: float | None
    ) -> dict[str, Any]:
        merged: dict[str, Any] = dict(params or {})
        if self.mailto:
            merged["mailto"] = self.mailto
        if self.api_key:
            merged["api_key"] = self.api_key
        return request_json(
            "GET",
            url,
            provider=self.provider.value,
            params=merged,
            timeout=timeout if timeout is not None else self.timeout,
            min_interval_seconds=get_settings().openalex_min_interval_seconds,
            on_attempt=self.on_attempt,
        )

    @classmethod
    def to_work(cls, payload: dict[str, Any]) -> ProviderWork:
        external_id = _short_id(str(payload.get("id") or ""))
        if not external_id:
            raise ProviderInvalidResponseError(
                "The provider returned a record with no identifier.",
                provider=cls.provider.value,
                keys=sorted(payload)[:10],
            )

        ids = payload.get("ids") or {}
        location = payload.get("primary_location") or {}
        source = location.get("source") or {}
        biblio = payload.get("biblio") or {}
        return ProviderWork(
            provider=cls.provider,
            external_id=external_id,
            title=str(payload.get("display_name") or payload.get("title") or "").strip(),
            doi=normalise_doi(str(payload.get("doi") or ids.get("doi") or "")),
            arxiv_id=_arxiv_id(payload),
            year=_int_or_none(payload.get("publication_year")),
            authors=tuple(
                str((entry.get("author") or {}).get("display_name") or "").strip()
                for entry in payload.get("authorships") or []
                if (entry.get("author") or {}).get("display_name")
            ),
            venue=(
                str(source.get("display_name") or location.get("raw_source_name") or "").strip()
                or None
            ),
            abstract=reconstruct_abstract(payload.get("abstract_inverted_index")),
            url=str(location.get("landing_page_url") or payload.get("doi") or "") or None,
            volume=_str_or_none(biblio.get("volume")),
            issue=_str_or_none(biblio.get("issue")),
            pages=_pages(biblio),
            work_type=_CSL_TYPE.get(str(payload.get("type") or ""), "document"),
            raw=payload,
        )


def reconstruct_abstract(inverted: Any) -> str | None:
    if not isinstance(inverted, dict) or not inverted:
        return None
    positions: dict[int, str] = {}
    for word, indexes in inverted.items():
        if not isinstance(word, str) or not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, int) and 0 <= index < _MAX_INVERTED_POSITIONS:
                positions[index] = word
    if not positions:
        return None
    text = " ".join(positions[key] for key in sorted(positions))
    return text.strip() or None


ARXIV_DOI_PREFIX = "10.48550/arXiv."

_ARXIV_MODERN = re.compile(r"^\d{4}\.\d{4,5}$")
_ARXIV_LEGACY = re.compile(r"^[a-z-]+(\.[A-Z]{2})?/\d{7}$")

_FILTER_PUNCTUATION = re.compile(r"[,:|!+()?*]+")
"""Characters OpenAlex reads as filter or query syntax rather than as words.

``,`` separates filters and ``:`` separates key from value. ``?`` and ``*`` are
Lucene wildcards, and a title carrying either is rejected outright: measured
live, `Can active memory replace attention?` answers 400 while the same title
without the question mark answers 200. That is a real paper title, and asking a
question in one is not unusual.

The set is exactly what the provider refuses, checked one character at a time:
quotes, braces, brackets, tildes, carets, slashes, hyphens and ampersands all
answer 200 and are left alone, because stripping punctuation the provider
accepts would quietly narrow the search.
"""


def _matching(work: ProviderWork, *, doi: str = "", arxiv_id: str = "") -> ProviderWork:
    if doi and work.doi and normalise_doi(work.doi) != doi:
        raise ProviderInvalidResponseError(
            "The provider returned a different work from the one requested.",
            provider=work.provider.value,
            requested=doi,
            returned=work.doi,
        )
    if arxiv_id:
        returned = normalise_arxiv(work.arxiv_id) or _arxiv_from_doi(work.doi)
        if returned and returned != arxiv_id:
            raise ProviderInvalidResponseError(
                "The provider returned a different preprint from the one requested.",
                provider=work.provider.value,
                requested=arxiv_id,
                returned=returned,
            )
    return work


def _arxiv_from_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    lowered = doi.casefold()
    prefix = ARXIV_DOI_PREFIX.casefold()
    return normalise_arxiv(lowered[len(prefix) :]) if lowered.startswith(prefix) else None


def normalise_arxiv(value: str | None) -> str | None:
    if not value:
        return None
    candidate = value.strip().removeprefix("arXiv:").removeprefix("arxiv:").strip()
    candidate = re.sub(r"v\d+$", "", candidate)
    if _ARXIV_MODERN.match(candidate) or _ARXIV_LEGACY.match(candidate):
        return candidate
    return None


def _filter_safe(title: str) -> str:
    return " ".join(_FILTER_PUNCTUATION.sub(" ", title).split())


def normalise_doi(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    text = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", text, flags=re.IGNORECASE)
    text = text.strip().rstrip(".")
    return text.casefold() if text.startswith("10.") else None


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = payload.get("results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


def _short_id(url: str) -> str:
    return url.rsplit("/", 1)[-1] if url else ""


def _arxiv_id(payload: dict[str, Any]) -> str | None:
    for location in payload.get("locations") or []:
        if not isinstance(location, dict):
            continue
        url = str(location.get("landing_page_url") or "")
        match = re.search(r"arxiv\.org/abs/([\w.\-/]+)", url, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _pages(biblio: dict[str, Any]) -> str | None:
    first, last = biblio.get("first_page"), biblio.get("last_page")
    if first and last:
        return f"{first}-{last}"
    return _str_or_none(first)


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value not in (None, "") else None
