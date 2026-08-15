"""Preserved blocks: figures, tables, formulas, lists, and notes.

A paper is not a stream of paragraphs. Tables carry the results, figures carry
the argument, and equations carry the method. An editing tool that quietly drops
them has not edited the paper, it has damaged it. So every non-prose block is
captured, is never sent to a model, is never mutated by an edit, and is
accounted for at export.

The three status values are deliberately separate. ``parse_status`` says how
well the block was understood; ``fidelity_status`` says what happens to it on the
way out. A block can be perfectly understood and still be impossible to
reproduce.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class BlockKind(StrEnum):
    FIGURE = "FIGURE"
    TABLE = "TABLE"
    FORMULA = "FORMULA"
    LIST = "LIST"
    NOTE = "NOTE"
    UNKNOWN = "UNKNOWN"


class BlockParseStatus(StrEnum):
    STRUCTURED = "STRUCTURED"

    PARTIAL = "PARTIAL"

    RAW_ONLY = "RAW_ONLY"


class FidelityStatus(StrEnum):
    """What export can promise about this block.

    ``EXACT`` means the canonical rendered representation is unchanged. Almost
    nothing earns it on a PDF-to-TEI pipeline, and claiming otherwise would be
    the kind of overstatement this system is built to avoid.

    ``CONVERTED`` is retained for old revisions, but means only that the block
    was reconstructed from extracted TEI. It does not assert that the PDF
    extractor recovered every symbol correctly.

    ``SOURCE_CROP`` means the block is rendered from a crop of the original PDF
    at the coordinates GROBID identified. It is visually faithful even when the
    TEI text representation is not mathematically trustworthy.

    ``UNRENDERABLE`` means the source asserts content this pipeline cannot
    reproduce. GROBID may emit ``<graphic>`` with coordinates but no image bytes;
    the parser first recovers an embedded raster image or nearby vector artwork
    from the source PDF. Only a block with no recoverable source representation
    remains unrenderable and blocks a fidelity export rather than being silently
    omitted.
    """

    EXACT = "EXACT"
    CONVERTED = "CONVERTED"
    SOURCE_CROP = "SOURCE_CROP"
    UNRENDERABLE = "UNRENDERABLE"


class SourceBox(BaseModel):
    """A GROBID PDF coordinate box (page numbers are one-based)."""

    model_config = ConfigDict(frozen=True)

    page: int
    x: float
    y: float
    width: float
    height: float


class PreservedBlock(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    kind: BlockKind
    order_index: int
    label: str | None = None
    caption: str | None = None

    normalized_content: str = ""

    raw_source: str = ""
    parse_status: BlockParseStatus = BlockParseStatus.STRUCTURED
    fidelity_status: FidelityStatus = FidelityStatus.CONVERTED

    missing_asset: bool = False
    source_boxes: tuple[SourceBox, ...] = ()
    source_asset: str | None = None
    table_rows: tuple[tuple[str, ...], ...] = ()

    @property
    def blocks_fidelity_export(self) -> bool:
        return self.fidelity_status is FidelityStatus.UNRENDERABLE
