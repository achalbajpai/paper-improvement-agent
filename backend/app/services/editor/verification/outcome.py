"""Whether the edit did what was asked, and what it cost to find out.

Falling short of the requested reduction is reported rather than presented as
success, and a provider that could not be reached is named."""

from __future__ import annotations

from app.domain.citation import SemanticParseStatus
from app.domain.document import Document
from app.domain.verification import (
    VerificationWarning,
    WarningCode,
)
from app.services.editor.verification.inputs import VerificationInputs, _Accumulator


def _check_target(inputs: VerificationInputs, state: _Accumulator, content_hash: str) -> None:
    """Did the edit do what was asked?

    Falling well short is reported rather than presented as success, because a
    section that was asked to lose a quarter of its words and lost a twentieth
    looks, in a diff, exactly like one that worked.
    """
    if inputs.achieved_fraction is None:
        return
    if inputs.achieved_fraction >= 0.6:
        return

    percentage = round(inputs.achieved_fraction * 100)
    state.warnings.append(
        VerificationWarning.build(
            WarningCode.TARGET_NOT_MET,
            (
                f"This edit achieved about {percentage}% of the requested reduction. "
                "The section is shorter, but not by as much as you asked for."
            ),
            ("target",),
            content_hash,
        )
    )


def _note_degradation(inputs: VerificationInputs, state: _Accumulator, content_hash: str) -> None:
    """A provider that was down narrowed the search, and the researcher is told.

    Silence here would let an outage read as "there was nothing to find", which
    is the single most misleading thing this system could say.
    """
    for degradation in inputs.degradations:
        state.warnings.append(
            VerificationWarning.build(
                WarningCode.PROVIDER_DEGRADED,
                (
                    f"{degradation.provider} was unavailable ({degradation.code}) during "
                    "this edit, so the literature search was narrower than usual."
                ),
                (degradation.provider,),
                content_hash,
            )
        )


def fidelity_warnings(document: Document, content_hash: str) -> list[VerificationWarning]:
    """Citations whose modifiers this system could not fully parse.

    Surfaced at export preflight; exposed here so acceptance and export agree on
    what "lossy" means rather than each deciding for itself.
    """
    lossy = sorted(
        citation_id
        for citation_id, node in document.citations.items()
        if node.semantic_parse_status is not SemanticParseStatus.STRUCTURED
    )
    if not lossy:
        return []
    return [
        VerificationWarning.build(
            WarningCode.FIDELITY_LOSS,
            (
                f"{len(lossy)} citation markers carry page numbers or notes this system "
                "could not fully parse, so the export may not reproduce them exactly."
            ),
            tuple(lossy),
            content_hash,
        )
    ]
