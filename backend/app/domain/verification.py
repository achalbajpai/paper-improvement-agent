"""Verification results, warnings, and warning identity.

Two kinds of outcome, and the difference is not severity but *who decides*.

* A **blocker** is something the system will not do at all. No acknowledgement
  clears it.
* A **warning** is a real consequence the researcher may accept. It requires an
  explicit acknowledgement naming that specific consequence.

Warning identity is the load-bearing part. An acknowledgement is bound to a
warning id that is derived from the warning's *content*, so acknowledging
"citation [12] will be removed" cannot be replayed against a later proposal that
removes a different citation, and cannot survive the proposal changing
underneath it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.domain.hashing import canonical_sha256


class CheckStatus(StrEnum):
    PASSED = "PASSED"
    WARNED = "WARNED"
    BLOCKED = "BLOCKED"

    NOT_RUN = "NOT_RUN"


class CheckName(StrEnum):
    PROTECTED_TOKENS = "PROTECTED_TOKENS"
    CITATION_PRESERVATION = "CITATION_PRESERVATION"
    SEMANTIC_ATTACHMENT = "SEMANTIC_ATTACHMENT"
    SEMANTIC_NOVELTY = "SEMANTIC_NOVELTY"
    NEW_CITATION_SUPPORT = "NEW_CITATION_SUPPORT"
    STRUCTURE_PRESERVATION = "STRUCTURE_PRESERVATION"
    BLOCK_PRESERVATION = "BLOCK_PRESERVATION"
    REFERENCE_COMPLETENESS = "REFERENCE_COMPLETENESS"
    PROSE_IMMUTABILITY = "PROSE_IMMUTABILITY"


class WarningCode(StrEnum):
    """Consequences a researcher can knowingly accept."""

    CITATION_REMOVED = "CITATION_REMOVED"

    REFERENCE_LEFT_UNCITED = "REFERENCE_LEFT_UNCITED"

    ATTACHMENT_INVALIDATED = "ATTACHMENT_INVALIDATED"

    NEW_CITATION_PARTIAL_SUPPORT = "NEW_CITATION_PARTIAL_SUPPORT"

    NEW_CITATION_SOURCE_UNCERTAIN = "NEW_CITATION_SOURCE_UNCERTAIN"

    TARGET_NOT_MET = "TARGET_NOT_MET"

    PROVIDER_DEGRADED = "PROVIDER_DEGRADED"

    FIDELITY_LOSS = "FIDELITY_LOSS"


class BlockerCode(StrEnum):
    """Things the system refuses to do, whatever the researcher says."""

    PROTECTED_TOKEN_VIOLATION = "PROTECTED_TOKEN_VIOLATION"
    CITATION_INVENTED = "CITATION_INVENTED"
    UNSUPPORTED_NOVELTY = "UNSUPPORTED_NOVELTY"
    ATTACHMENT_CONTRADICTED = "ATTACHMENT_CONTRADICTED"
    NEW_CITATION_UNSUPPORTED = "NEW_CITATION_UNSUPPORTED"
    NEW_REFERENCE_INCOMPLETE = "NEW_REFERENCE_INCOMPLETE"
    BLOCK_MUTATED = "BLOCK_MUTATED"
    STRUCTURE_DAMAGED = "STRUCTURE_DAMAGED"
    PROSE_MUTATED = "PROSE_MUTATED"
    IDENTITY_INVARIANT_VIOLATED = "IDENTITY_INVARIANT_VIOLATED"


class VerificationWarning(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    code: WarningCode

    message: str
    subject_ids: tuple[str, ...] = ()

    @staticmethod
    def build(
        code: WarningCode,
        message: str,
        subject_ids: tuple[str, ...],
        content_hash: str,
    ) -> VerificationWarning:
        """Construct a warning whose id is derived from its content.

        ``content_hash`` binds the id to the candidate this warning was computed
        against, so an acknowledgement collected for one proposal cannot be
        replayed against a regenerated one.
        """
        identity = canonical_sha256(
            {
                "code": code.value,
                "subject_ids": sorted(subject_ids),
                "content_hash": content_hash,
            }
        )
        return VerificationWarning(
            id=f"warn_{identity[:32]}",
            code=code,
            message=message,
            subject_ids=subject_ids,
        )


class VerificationBlocker(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: BlockerCode
    message: str
    subject_ids: tuple[str, ...] = ()


class VerificationCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: CheckName
    status: CheckStatus
    detail: str = ""

    prompt_version: str | None = None
    model: str | None = None
    provider: str | None = None
    """Which provider answered. Two providers serve models with the same name,
    and a fallback can answer a check the primary was configured for, so a model
    string alone cannot identify what made this judgement."""


class VerificationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    checks: tuple[VerificationCheck, ...] = Field(default_factory=tuple)
    blockers: tuple[VerificationBlocker, ...] = Field(default_factory=tuple)
    warnings: tuple[VerificationWarning, ...] = Field(default_factory=tuple)

    @property
    def required_warning_ids(self) -> tuple[str, ...]:
        """Exactly the acknowledgements acceptance will demand."""
        return tuple(sorted(warning.id for warning in self.warnings))

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def can_accept(self) -> bool:
        return not self.is_blocked
