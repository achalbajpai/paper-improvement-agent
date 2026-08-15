"""What a manuscript already cites, in every form a provider record can match.

Two paths need this question answered and would answer it differently if each
wrote its own: the missing-work reviewer must not recommend a paper the author
already cites, and the citation adder must not mint a second bibliography entry
for one. Two implementations would drift, and the one that drifted would be
telling a researcher something confidently wrong about their own document.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SourceRecord
from app.domain.document import Document
from app.domain.source import ProviderWork
from app.providers.openalex import normalise_doi
from app.services.citations.resolver import TITLE_MATCH_THRESHOLD, title_similarity


class BibliographyIndex:
    """Everything the manuscript already cites, in every form it can be matched on.

    A bibliography entry and a provider record agree on an identifier far less
    often than one would like -- GROBID may have recovered a DOI and no external
    id, or a title and neither -- so identity matching falls back to title
    similarity at the same threshold the reference resolver uses.
    """

    def __init__(self, keys: set[str], titles: tuple[str, ...]) -> None:
        self._keys = keys
        self._titles = titles

    @classmethod
    def of(cls, document: Document) -> BibliographyIndex:
        """What this manuscript must not be told to cite.

        The bibliography, plus the manuscript's own title. A published paper is
        in the providers' indexes, so a search on its own topic returns the
        paper itself -- and recommending an author their own work is the most
        obviously wrong suggestion the system could make.

        Matching includes titles because GROBID recovers a title far more often
        than a DOI -- on the corpus papers, frequently no DOI at all -- so
        identifier matching alone would let through a work plainly already
        cited.
        """
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
        """Also matching on the provider identities resolution already found."""
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
