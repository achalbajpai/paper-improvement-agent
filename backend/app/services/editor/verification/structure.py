"""Checks that the document still has the shape it started with.

Sections, paragraphs, preserved blocks, and the bibliography. Nothing here is a
judgement call: a section that vanished is a fact, and every finding is a
blocker rather than something the researcher can accept."""

from __future__ import annotations

from app.domain.verification import (
    BlockerCode,
    CheckName,
    CheckStatus,
    VerificationBlocker,
    VerificationCheck,
)
from app.services.editor.verification.inputs import VerificationInputs, _Accumulator


def _check_structure(inputs: VerificationInputs, state: _Accumulator) -> None:
    """Structural integrity, computed from the two documents.

    None of this trusts the editing code's account of what it did: the candidate
    is inspected as a document, exactly as it would be at export.
    """
    problems: list[str] = []
    candidate = inputs.candidate

    base_sections = [section.id for section in inputs.base.sections]
    if [section.id for section in candidate.sections] != base_sections:
        problems.append("the section sequence changed")

    placed: list[str] = []
    for paragraph in candidate.paragraphs():
        placed.extend(paragraph.citation_ids)
    duplicates = sorted({item for item in placed if placed.count(item) > 1})
    if duplicates:
        problems.append(f"occurrences appear more than once: {', '.join(duplicates)}")

    unknown = sorted(set(placed) - set(candidate.citations))
    if unknown:
        problems.append(f"occurrences with no registry entry: {', '.join(unknown)}")

    reference_ids = {reference.id for reference in candidate.references}
    dangling = sorted(
        {
            item.reference_id
            for node in candidate.citations.values()
            for item in node.items
            if item.reference_id is not None and item.reference_id not in reference_ids
        }
    )
    if dangling:
        problems.append(f"citations point at missing references: {', '.join(dangling)}")

    if problems:
        state.blockers.append(
            VerificationBlocker(
                code=BlockerCode.STRUCTURE_DAMAGED,
                message="The candidate is not a well-formed document: " + "; ".join(problems),
            )
        )
    state.checks.append(
        VerificationCheck(
            name=CheckName.STRUCTURE_PRESERVATION,
            status=CheckStatus.BLOCKED if problems else CheckStatus.PASSED,
            detail=(
                "; ".join(problems)
                if problems
                else f"{len(base_sections)} sections and every citation reference intact."
            ),
        )
    )


def _check_blocks(inputs: VerificationInputs, state: _Accumulator) -> None:
    """Tables, figures, and equations are carried, never edited.

    They are preserved verbatim precisely because this system cannot understand
    them well enough to change them safely.
    """
    mutated = inputs.delta.touches_blocks or inputs.base.blocks != inputs.candidate.blocks
    if mutated:
        state.blockers.append(
            VerificationBlocker(
                code=BlockerCode.BLOCK_MUTATED,
                message="The edit altered a preserved block, which is never permitted.",
            )
        )
    state.checks.append(
        VerificationCheck(
            name=CheckName.BLOCK_PRESERVATION,
            status=CheckStatus.BLOCKED if mutated else CheckStatus.PASSED,
            detail=f"{len(inputs.base.blocks)} preserved blocks carried through unchanged.",
        )
    )


def _check_new_references(
    inputs: VerificationInputs, state: _Accumulator, content_hash: str
) -> None:
    """New bibliography entries must be complete enough for a reader to use them.

    The other invariant on a new reference -- ``csl.id == id``, whose violation
    makes citeproc drop the bibliography entry while the citation still renders --
    is enforced by ``ReferenceRecord`` itself, at construction and at every load
    from stored JSON. Re-checking it here would imply it is reachable, and would
    be one more place to keep in step with the model.
    """
    existing = {reference.id for reference in inputs.base.references}
    added = [reference for reference in inputs.candidate.references if reference.id not in existing]

    incomplete = [
        reference.id for reference in added if not reference.csl.is_complete_for_insertion()
    ]

    if incomplete:
        state.blockers.append(
            VerificationBlocker(
                code=BlockerCode.NEW_REFERENCE_INCOMPLETE,
                message=(
                    "A citation was added for a work whose metadata is too incomplete "
                    "for a reader to look it up."
                ),
                subject_ids=tuple(incomplete),
            )
        )
    state.checks.append(
        VerificationCheck(
            name=CheckName.REFERENCE_COMPLETENESS,
            status=CheckStatus.BLOCKED if incomplete else CheckStatus.PASSED,
            detail=f"{len(added)} new references, each complete enough to cite.",
        )
    )
