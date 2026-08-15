"""Review anchors, verdicts, and findings.

Two anchor types, both of which exist so that no quoted text ever originates
from a model.

* ``ClaimAnchor`` points at a sentence in the manuscript. The model returns a
  sentence id; the server looks up the text.
* ``EvidenceAnchor`` points at a span of a provider-supplied abstract, snapshotted
  in ``source_records``. The model returns a span id; the server looks up the
  text.

Because both are id-based, "the model quoted something the source does not say"
is not a class of bug that can occur here.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class SupportVerdict(StrEnum):
    """Whether a cited work supports the claim it is attached to.

    ``UNSUPPORTED`` is deliberately absent. Failing to find support in an
    abstract is not evidence that the cited paper lacks it -- the material may
    be in the full text -- and reporting it as unsupported would launder absence
    of evidence into a finding against the researcher's citation. Every verdict
    is an abstract-level assessment and is labelled as one.
    """

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE = "UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE"

    EVIDENCE_UNAVAILABLE = "EVIDENCE_UNAVAILABLE"

    SOURCE_IDENTITY_UNCERTAIN = "SOURCE_IDENTITY_UNCERTAIN"

    SOURCE_UNRESOLVED = "SOURCE_UNRESOLVED"


MODEL_SELECTABLE_VERDICTS = (
    SupportVerdict.SUPPORTED,
    SupportVerdict.PARTIALLY_SUPPORTED,
    SupportVerdict.CONTRADICTED,
    SupportVerdict.UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE,
)


class ClaimAnchor(BaseModel):
    """A position in the manuscript, plus enough to detect that it moved."""

    model_config = ConfigDict(frozen=True)

    paragraph_id: str
    sentence_index: int
    sentence_id: str

    text_hash: str

    segmenter_version: str


class EvidenceAnchor(BaseModel):
    """A span of a snapshotted provider abstract."""

    model_config = ConfigDict(frozen=True)

    source_record_id: str
    span_id: str
    char_start: int
    char_end: int
    text_hash: str


class FindingKind(StrEnum):
    CITATION_SUPPORT = "CITATION_SUPPORT"

    UNCITED_CLAIM = "UNCITED_CLAIM"

    MISSING_WORK = "MISSING_WORK"

    UNRESOLVED_REFERENCE = "UNRESOLVED_REFERENCE"

    UNLINKED_CITATION = "UNLINKED_CITATION"


class ReviewFinding(BaseModel):
    """One reviewed (claim, occurrence, reference) triple.

    Support is evaluated per triple rather than per claim, because ``[2, 5]``
    is two distinct assertions of support and collapsing them would hide which
    one is weak.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    kind: FindingKind
    claim_anchor: ClaimAnchor

    occurrence_id: str | None = None
    reference_id: str | None = None
    verdict: SupportVerdict | None = None
    evidence: tuple[EvidenceAnchor, ...] = ()

    reason: str = ""

    suggested_source_record_ids: tuple[str, ...] = ()
    """Snapshotted provider records offered as work this claim does not cite.

    Source record ids, never bibliography reference ids: a suggestion is by
    definition not yet in the manuscript, and pointing at a reference id would
    claim it was.
    """

    suggestion_rationales: tuple[str, ...] = ()
    """Positionally paired with the suggestions. Model-authored interpretation,
    shown as such -- the works themselves come from provider records."""

    provider: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    """Which provider, model and prompt produced the model-authored part of this
    finding. A verdict nobody can trace back is one nobody can re-examine, and a
    fallback provider can answer a call the primary was configured for."""


class ProviderDegradation(BaseModel):
    """A provider that was unavailable during a run.

    Always reported. A review that silently skipped a provider is a review whose
    coverage the researcher cannot judge.
    """

    model_config = ConfigDict(frozen=True)

    provider: str
    code: str
    detail: str = ""
