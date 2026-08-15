"""Candidate revisions and proposal state.

The Candidate Revision is the mechanism that makes an agentic editor safe to run
on someone's paper: an edit produces a complete proposed document that is
verified as a whole and either accepted atomically or discarded. Nothing is ever
half-applied, because there is no intermediate state to be half-applied *from*.

Everything needed to verify, display, and apply the edit is captured in the
snapshot. Acceptance re-checks the snapshot's identity rather than recomputing
the edit, so what the researcher approved is exactly what gets stored.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, computed_field

from app.domain.delta import ComputedEditDelta
from app.domain.document import Document
from app.domain.hashing import canonical_sha256
from app.domain.verification import VerificationResult


class ProposalState(StrEnum):
    PENDING = "PENDING"

    AWAITING_DECISION = "AWAITING_DECISION"

    BLOCKED = "BLOCKED"

    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"

    FAILED = "FAILED"

    SUPERSEDED = "SUPERSEDED"


TERMINAL_STATES = frozenset(
    {
        ProposalState.ACCEPTED,
        ProposalState.REJECTED,
        ProposalState.FAILED,
        ProposalState.SUPERSEDED,
        ProposalState.BLOCKED,
    }
)


class SkippedParagraph(BaseModel):
    model_config = ConfigDict(frozen=True)

    paragraph_id: str
    reason: str


class EditScope(BaseModel):
    """What the command was resolved to act on, and what it deliberately left alone.

    Derived from the planner, which is arithmetic over the parsed document with
    no model in it. That makes this answerable before any rewriting happens and
    identical whether the rewrite then succeeds or fails.

    It exists because "shorten the introduction" is a command whose blast radius
    the researcher cannot otherwise see until after the fact. A skipped paragraph
    is as much a part of the answer as a targeted one -- silence about it reads as
    an oversight rather than the deliberate exclusion it is.
    """

    model_config = ConfigDict(frozen=True)

    section_id: str
    section_title: str = ""

    paragraph_id: str | None = None

    targeted_paragraph_ids: tuple[str, ...] = ()
    skipped: tuple[SkippedParagraph, ...] = ()

    words_before: int = 0
    words_target: int = 0

    section_paragraph_count: int = 0


class CandidateRevisionSnapshot(BaseModel):
    """A complete proposed document, with the evidence that it is safe.

    Immutable and self-contained. Acceptance validates ``snapshot_sha256`` and
    the base revision, then stores ``document`` as the new revision; it never
    re-runs the edit, so the researcher's decision and the stored result cannot
    diverge.
    """

    model_config = ConfigDict(frozen=True)

    base_revision_id: str
    document: Document
    delta: ComputedEditDelta
    verification: VerificationResult

    explanation: str = ""

    scope: EditScope | None = None

    @computed_field
    @property
    def snapshot_sha256(self) -> str:
        """Identity of everything the researcher was shown.

        Covers the document, the delta, and the verification result together. A
        proposal regenerated with different warnings is a different snapshot even
        if the resulting prose is identical, which is what stops an
        acknowledgement from being carried across.

        ``scope`` is deliberately outside it. It describes which paragraphs the
        planner selected, and every one of those decisions is already visible in
        the delta and the document that the hash does cover; hashing a second,
        derived description of the same edit would only create a way for the two
        to disagree.
        """
        return canonical_sha256(
            {
                "base_revision_id": self.base_revision_id,
                "document": self.document.model_dump(mode="json"),
                "delta": self.delta.model_dump(mode="json"),
                "verification": self.verification.model_dump(mode="json"),
            }
        )
