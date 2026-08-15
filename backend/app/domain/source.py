"""Provider records, as this system stores them.

A ``SourceRecordSnapshot`` is a copy, taken at the moment the record was used and
never refreshed. That is not caching, it is correctness: evidence anchors address
a span of the abstract by character offsets, and a provider silently editing an
abstract would move every offset so that quoted evidence drifts away from the
claim it was about. The snapshot is the thing the verdict was actually made
against.

``ProviderWork`` is the neutral shape both adapters return, so nothing downstream
learns whether a work came from OpenAlex or Semantic Scholar in order to read its
title.

Pure: no HTTP, no database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.reference import CSLItem


class ProviderName(StrEnum):
    OPENALEX = "OPENALEX"
    SEMANTIC_SCHOLAR = "SEMANTIC_SCHOLAR"


class RetrievalPurpose(StrEnum):
    """Why a provider was called. Recorded so budgets can be read per purpose."""

    RESOLVE_REFERENCE = "RESOLVE_REFERENCE"
    FETCH_ABSTRACT = "FETCH_ABSTRACT"
    SEARCH_CANDIDATES = "SEARCH_CANDIDATES"


class ProviderWork(BaseModel):
    """One work as a provider described it, normalised across providers."""

    model_config = ConfigDict(frozen=True)

    provider: ProviderName

    external_id: str
    title: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    authors: tuple[str, ...] = ()
    venue: str | None = None

    abstract: str | None = None
    url: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    work_type: str = "article-journal"

    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_abstract(self) -> bool:
        return bool(self.abstract and self.abstract.strip())

    def identity_keys(self) -> tuple[str, ...]:
        """Every identifier this work can be matched on, strongest first."""
        keys: list[str] = []
        if self.doi:
            keys.append(f"doi:{self.doi.casefold()}")
        if self.arxiv_id:
            keys.append(f"arxiv:{self.arxiv_id.casefold()}")
        keys.append(f"{self.provider.value.casefold()}:{self.external_id.casefold()}")
        return tuple(keys)

    def to_csl(self, reference_id: str) -> CSLItem:
        """A CSL item for this work, ready to be inserted into a bibliography."""
        fields: dict[str, Any] = {
            "id": reference_id,
            "type": self.work_type,
            "title": self.title,
            "author": [{"literal": name} for name in self.authors],
        }
        if self.year is not None:
            fields["issued"] = {"date-parts": [[self.year]]}
        if self.venue:
            fields["container-title"] = self.venue
        if self.doi:
            fields["DOI"] = self.doi
        if self.url:
            fields["URL"] = self.url
        for key, value in (("volume", self.volume), ("issue", self.issue), ("page", self.pages)):
            if value:
                fields[key] = value
        custom: dict[str, Any] = {"provider": self.provider.value, "external_id": self.external_id}
        if self.arxiv_id:
            custom["arxiv_id"] = self.arxiv_id
        fields["custom"] = custom
        return CSLItem.model_validate(fields)


class SourceRecordSnapshot(BaseModel):
    """A provider record frozen at the moment it was used."""

    model_config = ConfigDict(frozen=True)

    id: str
    provider: ProviderName
    external_id: str
    doi: str | None = None
    title: str = ""

    abstract: str | None = None
    url: str | None = None
    csl: dict[str, Any] = Field(default_factory=dict)
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @classmethod
    def of(cls, record_id: str, work: ProviderWork) -> SourceRecordSnapshot:
        return cls(
            id=record_id,
            provider=work.provider,
            external_id=work.external_id,
            doi=work.doi,
            title=work.title,
            abstract=work.abstract,
            url=work.url,
            csl=work.to_csl(record_id).model_dump(mode="json", by_alias=True),
            raw_payload=work.raw,
        )
