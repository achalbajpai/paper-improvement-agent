from __future__ import annotations

from sqlalchemy.orm import Session

from app.domain.hashing import canonical_sha256
from app.domain.verification import VerificationResult
from app.providers.llm import StructuredLLM
from app.services.deadline import Deadline
from app.services.editor.verification.attachment import _check_attachment
from app.services.editor.verification.citations import (
    _check_citation_preservation,
    _check_new_citation_support,
)
from app.services.editor.verification.inputs import (
    INTACT,
    UNVERIFIABLE,
    VerificationInputs,
    _Accumulator,
    changed_kinds,
)
from app.services.editor.verification.outcome import (
    _check_target,
    _note_degradation,
    fidelity_warnings,
)
from app.services.editor.verification.structure import (
    _check_blocks,
    _check_new_references,
    _check_structure,
)

__all__ = [
    "INTACT",
    "UNVERIFIABLE",
    "VerificationInputs",
    "changed_kinds",
    "fidelity_warnings",
    "verify",
]


def verify(
    inputs: VerificationInputs,
    *,
    session: Session | None = None,
    llm: StructuredLLM | None = None,
    deadline: Deadline | None = None,
) -> VerificationResult:

    content_hash = canonical_sha256(
        {
            "candidate": inputs.candidate.model_dump(mode="json"),
            "delta": inputs.delta.model_dump(mode="json"),
        }
    )
    state = _Accumulator(checks=list(inputs.prior_checks))

    _check_structure(inputs, state)
    _check_blocks(inputs, state)
    _check_new_references(inputs, state, content_hash)
    _check_citation_preservation(inputs, state, content_hash)
    _check_new_citation_support(inputs, state, content_hash)
    _check_attachment(inputs, state, content_hash, session=session, llm=llm, deadline=deadline)
    _check_target(inputs, state, content_hash)
    _note_degradation(inputs, state, content_hash)

    return VerificationResult(
        checks=tuple(state.checks),
        blockers=tuple(state.blockers),
        warnings=tuple(state.warnings),
    )
