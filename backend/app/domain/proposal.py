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
        return canonical_sha256(
            {
                "base_revision_id": self.base_revision_id,
                "document": self.document.model_dump(mode="json"),
                "delta": self.delta.model_dump(mode="json"),
                "verification": self.verification.model_dump(mode="json"),
            }
        )
