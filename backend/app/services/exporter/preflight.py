from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.block import FidelityStatus, PreservedBlock
from app.domain.citation import SemanticParseStatus
from app.domain.document import Document
from app.domain.hashing import canonical_sha256
from app.domain.lifecycle import CitationStyle
from app.domain.verification import VerificationWarning, WarningCode
from app.domain.wording import is_are, plural

BLOCK_UNRENDERABLE_BLOCK = "UNRENDERABLE_BLOCK"
BLOCK_NO_STYLE_SELECTED = "NO_STYLE_SELECTED"
BLOCK_EMPTY_DOCUMENT = "EMPTY_DOCUMENT"


@dataclass(frozen=True)
class PreflightBlocker:
    code: str
    message: str
    subject_ids: tuple[str, ...] = ()


@dataclass
class Preflight:
    revision_id: str
    citation_style: CitationStyle | None
    blockers: list[PreflightBlocker] = field(default_factory=list)
    warnings: list[VerificationWarning] = field(default_factory=list)

    @property
    def can_export(self) -> bool:
        return not self.blockers

    @property
    def required_warning_ids(self) -> tuple[str, ...]:
        return tuple(sorted(warning.id for warning in self.warnings))


def check(
    document: Document,
    *,
    revision_id: str,
    citation_style: CitationStyle | None,
    retained_uncited_ids: tuple[str, ...] = (),
    dropped_added_ids: tuple[str, ...] = (),
) -> Preflight:
    content_hash = canonical_sha256(
        {"revision": revision_id, "style": citation_style.value if citation_style else ""}
    )
    result = Preflight(revision_id=revision_id, citation_style=citation_style)

    if citation_style is None:
        result.blockers.append(
            PreflightBlocker(
                code=BLOCK_NO_STYLE_SELECTED,
                message="Choose a citation style before exporting.",
            )
        )

    if not document.sections:
        result.blockers.append(
            PreflightBlocker(
                code=BLOCK_EMPTY_DOCUMENT,
                message="This revision has no sections to export.",
            )
        )

    unrenderable = [document.blocks[block_id] for block_id in document.unrenderable_block_ids()]
    if unrenderable:
        result.blockers.append(
            PreflightBlocker(
                code=BLOCK_UNRENDERABLE_BLOCK,
                message=(
                    f"{plural(len(unrenderable), 'figure')} declared an image that the "
                    "source PDF did not provide, so this manuscript cannot be exported "
                    "at full fidelity."
                ),
                subject_ids=tuple(block.id for block in unrenderable),
            )
        )

    result.warnings.extend(_fidelity_warnings(document, content_hash))
    if retained_uncited_ids:
        result.warnings.append(
            VerificationWarning.build(
                WarningCode.REFERENCE_LEFT_UNCITED,
                (
                    plural(
                        len(retained_uncited_ids),
                        "original bibliography entry",
                        "original bibliography entries",
                    )
                    + f" {is_are(len(retained_uncited_ids))} no longer cited in the text, "
                    "and will be retained so the export does not silently drop source "
                    "material."
                ),
                retained_uncited_ids,
                content_hash,
            )
        )
    if dropped_added_ids:
        result.warnings.append(
            VerificationWarning.build(
                WarningCode.FIDELITY_LOSS,
                (
                    "The text no longer cites "
                    f"{plural(len(dropped_added_ids), 'reference')} this system added, "
                    "so they will not appear in the bibliography."
                ),
                dropped_added_ids,
                content_hash,
            )
        )
    return result


def _fidelity_warnings(document: Document, content_hash: str) -> list[VerificationWarning]:
    warnings: list[VerificationWarning] = []

    partial = document.citations_by_parse_status(SemanticParseStatus.PARTIAL_MODIFIERS)
    if partial:
        warnings.append(
            VerificationWarning.build(
                WarningCode.FIDELITY_LOSS,
                (
                    "Page numbers or notes on "
                    f"{plural(len(partial), 'citation marker')} could not be parsed "
                    "confidently, and may not be reproduced exactly."
                ),
                partial,
                content_hash,
            )
        )

    raw_only = document.citations_by_parse_status(SemanticParseStatus.RAW_ONLY)
    if raw_only:
        warnings.append(
            VerificationWarning.build(
                WarningCode.FIDELITY_LOSS,
                (
                    f"{plural(len(raw_only), 'citation marker')} could not be parsed "
                    "into structured citations. They render as the author wrote them, "
                    "but will not be reformatted into the chosen style."
                ),
                raw_only,
                content_hash,
            )
        )

    converted = [block for block in document.blocks.values() if _is_converted(block)]
    if converted:
        warnings.append(
            VerificationWarning.build(
                WarningCode.FIDELITY_LOSS,
                (
                    "The export reproduces "
                    + plural(
                        len(converted),
                        "figure, table, or equation",
                        "figures, tables, and equations",
                    )
                    + " from reconstructed extraction rather than a source-backed crop. "
                    "Symbols, structure, and appearance may differ; review this draft "
                    "against the original before treating it as a final manuscript."
                ),
                tuple(block.id for block in converted),
                content_hash,
            )
        )
    return warnings


def _is_converted(block: PreservedBlock) -> bool:
    return block.fidelity_status is FidelityStatus.CONVERTED
