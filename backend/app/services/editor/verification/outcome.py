from __future__ import annotations

from app.domain.citation import SemanticParseStatus
from app.domain.document import Document
from app.domain.verification import (
    VerificationWarning,
    WarningCode,
)
from app.services.editor.verification.inputs import VerificationInputs, _Accumulator


def _check_target(inputs: VerificationInputs, state: _Accumulator, content_hash: str) -> None:
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
