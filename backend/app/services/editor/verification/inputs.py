from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.delta import ChangeKind, ComputedEditDelta
from app.domain.document import Document
from app.domain.review import ProviderDegradation, SupportVerdict
from app.domain.verification import (
    VerificationBlocker,
    VerificationCheck,
    VerificationWarning,
)
from app.services.editor.addcite import AddedCitation

INTACT = frozenset({SupportVerdict.SUPPORTED})

UNVERIFIABLE = frozenset(
    {
        SupportVerdict.UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE,
        SupportVerdict.EVIDENCE_UNAVAILABLE,
        SupportVerdict.SOURCE_IDENTITY_UNCERTAIN,
        SupportVerdict.SOURCE_UNRESOLVED,
    }
)


@dataclass
class VerificationInputs:
    base: Document
    candidate: Document
    delta: ComputedEditDelta

    paper_id: str = ""
    """Scopes snapshot lookup. Source records are per-paper, and matching one by
    identifier alone can return an abstract snapshotted for a different paper."""

    added: tuple[AddedCitation, ...] = ()

    degradations: tuple[ProviderDegradation, ...] = ()

    achieved_fraction: float | None = None

    prior_checks: tuple[VerificationCheck, ...] = ()


@dataclass
class _Accumulator:
    checks: list[VerificationCheck] = field(default_factory=list)
    blockers: list[VerificationBlocker] = field(default_factory=list)
    warnings: list[VerificationWarning] = field(default_factory=list)


def changed_kinds(delta: ComputedEditDelta) -> tuple[ChangeKind, ...]:
    return tuple(sorted({change.kind for change in delta.changes}))
