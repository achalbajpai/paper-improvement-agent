from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SourceRecord
from app.domain.document import Document
from app.domain.source import ProviderWork
from app.providers.openalex import normalise_doi
from app.services.citations.resolver import TITLE_MATCH_THRESHOLD, title_similarity


class BibliographyIndex:
    def __init__(self, keys: set[str], titles: tuple[str, ...]) -> None:
        self._keys = keys
        self._titles = titles

    @classmethod
    def of(cls, document: Document) -> BibliographyIndex:
        keys: set[str] = set()
        titles: list[str] = []

        if document.title:
            titles.append(document.title)

        for reference in document.references:
            doi = normalise_doi(reference.csl.DOI)
            if doi:
                keys.add(f"doi:{doi.casefold()}")
            if reference.csl.title:
                titles.append(reference.csl.title)

        return cls(keys, tuple(titles))

    @classmethod
    def with_snapshots(
        cls, session: Session, paper_id: str, document: Document
    ) -> BibliographyIndex:
        base = cls.of(document)
        keys = set(base._keys)
        titles = list(base._titles)
        record_ids = [
            reference.source_record_id
            for reference in document.references
            if reference.source_record_id
        ]

        if record_ids:
            records = (
                session.execute(
                    select(SourceRecord).where(
                        SourceRecord.paper_id == paper_id, SourceRecord.id.in_(record_ids)
                    )
                )
                .scalars()
                .all()
            )
            for record in records:
                keys.add(f"{record.provider.casefold()}:{record.external_id.casefold()}")
                if record.doi:
                    keys.add(f"doi:{record.doi.casefold()}")
                if record.title:
                    titles.append(record.title)

        return cls(keys, tuple(titles))

    def contains(self, work: ProviderWork) -> bool:
        if any(key in self._keys for key in work.identity_keys()):
            return True
        if not work.title:
            return False
        return any(
            title_similarity(work.title, title) >= TITLE_MATCH_THRESHOLD for title in self._titles
        )
