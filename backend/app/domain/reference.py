"""References and their canonical CSL form.

One rule governs this module: **every reference carries a valid CSL item, with no
exceptions.** A reference GROBID could not parse still becomes a CSL item of type
``document`` whose raw text is preserved.

The alternative -- an optional CSL field -- pushes an ``if csl is None`` branch
into the exporter, the resolver, the reviewer, and the UI, and the one that gets
forgotten silently drops a bibliography entry from the author's paper.

The raw text goes in ``custom``, never in ``note``: several CSL styles render
``note``, so parking unparsed text there would print parser debris into a
formatted bibliography.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.errors import IdentityInvariantError
from app.domain.ids import Origin, origin_of

RAW_TEXT_KEY = "raw_text"


class NormalizationStatus(StrEnum):
    COMPLETE = "COMPLETE"

    PARTIAL = "PARTIAL"

    RAW_ONLY = "RAW_ONLY"


class ResolutionMethod(StrEnum):
    """How a reference was matched to a provider record, strongest first."""

    DOI = "DOI"
    ARXIV = "ARXIV"
    PUBMED = "PUBMED"
    EXTERNAL_ID = "EXTERNAL_ID"
    TITLE_AUTHOR_YEAR = "TITLE_AUTHOR_YEAR"
    TITLE_ONLY = "TITLE_ONLY"
    UNRESOLVED = "UNRESOLVED"


class ResolutionConfidence(StrEnum):
    """Confidence is read off observable evidence, never asserted by a model."""

    CERTAIN = "CERTAIN"

    PROBABLE = "PROBABLE"

    UNCERTAIN = "UNCERTAIN"
    NONE = "NONE"


class CSLItem(BaseModel):
    """A CSL-JSON item.

    CSL-JSON is an open schema, so unknown keys are preserved rather than
    dropped: discarding a field we do not model would corrupt a bibliography we
    were asked to protect.
    """

    model_config = ConfigDict(extra="allow")

    id: str
    type: str = "document"
    title: str | None = None
    author: list[dict[str, Any]] = Field(default_factory=list)
    issued: dict[str, Any] | None = None
    container_title: str | None = Field(default=None, alias="container-title")
    DOI: str | None = None
    URL: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)

    @property
    def year(self) -> int | None:
        parts = (self.issued or {}).get("date-parts") or []
        if parts and parts[0]:
            first = parts[0][0]
            return int(first) if isinstance(first, int | str) and str(first).isdigit() else None
        return None

    @property
    def raw_text(self) -> str:
        value = self.custom.get(RAW_TEXT_KEY, "")
        return str(value)

    def is_complete_for_insertion(self) -> bool:
        """Whether this item may be *added* to a manuscript.

        Adding a citation is a stronger act than preserving one. A reference the
        author already chose is kept whatever its quality, but a reference this
        system introduces must be complete enough that a reader can find the
        work: a title, at least one author, and a year.
        """
        return bool(self.title) and bool(self.author) and self.year is not None


class ReferenceRecord(BaseModel):
    """One bibliography entry, as parsed and as resolved."""

    model_config = ConfigDict(frozen=True)

    id: str

    csl: CSLItem

    raw_text: str = ""

    bibliography_order: int = 0
    normalization_status: NormalizationStatus = NormalizationStatus.COMPLETE
    resolution_method: ResolutionMethod = ResolutionMethod.UNRESOLVED
    resolution_confidence: ResolutionConfidence = ResolutionConfidence.NONE

    source_record_id: str | None = None

    @model_validator(mode="after")
    def _csl_id_matches(self) -> ReferenceRecord:
        """``csl.id`` must equal ``id``.

        Export drives the bibliography from ``csl.id``, and citations refer to
        the record by ``id``. If the two disagree, citeproc silently emits no
        bibliography entry for that reference: the citation renders, the entry
        vanishes, and nothing errors. This check is why that cannot happen.
        """
        if self.csl.id != self.id:
            raise IdentityInvariantError(
                "A reference's CSL id must equal its record id.",
                reference_id=self.id,
                csl_id=self.csl.id,
            )
        return self

    @property
    def origin(self) -> Origin | None:
        return origin_of(self.id)

    @property
    def is_resolved(self) -> bool:
        """Whether the identity of the cited work is established.

        UNCERTAIN is deliberately *not* resolved. A weak title match is not
        knowledge of which paper this is, and a support verdict issued against
        the wrong paper is worse than no verdict.
        """
        return self.resolution_confidence in (
            ResolutionConfidence.CERTAIN,
            ResolutionConfidence.PROBABLE,
        )


def csl_for_unparseable(reference_id: str, raw_text: str) -> CSLItem:
    """A valid CSL item for a reference that could not be parsed at all."""
    return CSLItem(id=reference_id, type="document", custom={RAW_TEXT_KEY: raw_text})
