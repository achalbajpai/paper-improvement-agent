"""Checks about which citations exist and what supports the new ones.

Removal is a consequence the researcher can accept; a citation added without
evidence behind it is not."""

from __future__ import annotations

from app.domain.document import Document
from app.domain.review import SupportVerdict
from app.domain.verification import (
    BlockerCode,
    CheckName,
    CheckStatus,
    VerificationBlocker,
    VerificationCheck,
    VerificationWarning,
    WarningCode,
)
from app.services.editor.verification.inputs import (
    UNVERIFIABLE,
    VerificationInputs,
    _Accumulator,
)


def _check_citation_preservation(
    inputs: VerificationInputs, state: _Accumulator, content_hash: str
) -> None:
    """Removal is allowed. Silent removal is not.

    Shortening a section legitimately drops citations along with the sentences
    that carried them, so this warns rather than blocks -- but it warns with the
    marker the author actually wrote, because "one citation was removed" is not
    something a researcher can check.
    """
    removed = inputs.delta.removed_citation_ids
    for citation_id in removed:
        node = inputs.base.citations.get(citation_id)
        marker = (node.raw_marker if node and node.raw_marker else citation_id).strip()
        state.warnings.append(
            VerificationWarning.build(
                WarningCode.CITATION_REMOVED,
                f"The citation {marker} will no longer appear in the manuscript.",
                (citation_id,),
                content_hash,
            )
        )

    for reference_id in _left_uncited(inputs):
        reference = inputs.base.reference(reference_id)
        title = (reference.csl.title if reference else "") or reference_id
        state.warnings.append(
            VerificationWarning.build(
                WarningCode.REFERENCE_LEFT_UNCITED,
                (
                    f'"{title}" is no longer cited anywhere in the manuscript. It will be '
                    "retained in the bibliography so the export does not drop source material."
                ),
                (reference_id,),
                content_hash,
            )
        )

    state.checks.append(
        VerificationCheck(
            name=CheckName.CITATION_PRESERVATION,
            status=CheckStatus.WARNED if removed else CheckStatus.PASSED,
            detail=(
                f"{len(removed)} citation occurrences removed, each reported."
                if removed
                else "No citation the author wrote was removed."
            ),
        )
    )


def _left_uncited(inputs: VerificationInputs) -> tuple[str, ...]:
    """References that were cited before the edit and are cited no longer."""
    before = _cited_reference_ids(inputs.base)
    after = _cited_reference_ids(inputs.candidate)
    return tuple(sorted(before - after))


def _cited_reference_ids(document: Document) -> set[str]:
    return {
        item.reference_id
        for node in document.citations.values()
        for item in node.items
        if item.reference_id is not None
    }


def _check_new_citation_support(
    inputs: VerificationInputs, state: _Accumulator, content_hash: str
) -> None:
    """A citation this system added has to have earned its place.

    The bar is higher than for a citation the author chose: adding one is an
    assertion this system is making on the researcher's behalf, so anything short
    of positive evidence from the snapshotted abstract either warns or blocks.
    """
    if not inputs.added:
        return

    unsupported = [
        item.citation_id
        for item in inputs.added
        if item.verdict is SupportVerdict.CONTRADICTED or item.verdict in UNVERIFIABLE
    ]
    partial = [item for item in inputs.added if item.verdict is SupportVerdict.PARTIALLY_SUPPORTED]

    if unsupported:
        state.blockers.append(
            VerificationBlocker(
                code=BlockerCode.NEW_CITATION_UNSUPPORTED,
                message=(
                    "A citation was added for a work whose abstract does not support "
                    "the sentence it was attached to."
                ),
                subject_ids=tuple(unsupported),
            )
        )

    for item in partial:
        title = item.reference.csl.title or item.reference.id
        state.warnings.append(
            VerificationWarning.build(
                WarningCode.NEW_CITATION_PARTIAL_SUPPORT,
                (
                    f'"{title}" was added to a sentence its abstract supports only in '
                    "part. The full text may or may not support the rest."
                ),
                (item.citation_id,),
                content_hash,
            )
        )

    state.checks.append(
        VerificationCheck(
            name=CheckName.NEW_CITATION_SUPPORT,
            status=(
                CheckStatus.BLOCKED
                if unsupported
                else CheckStatus.WARNED
                if partial
                else CheckStatus.PASSED
            ),
            detail=(
                f"{len(inputs.added)} added citations checked against their "
                f"snapshotted abstracts; {len(partial)} partially supported."
            ),
        )
    )
