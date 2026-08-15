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
    EXACT = "EXACT"
    CONVERTED = "CONVERTED"
    SOURCE_CROP = "SOURCE_CROP"
    UNRENDERABLE = "UNRENDERABLE"


class SourceBox(BaseModel):
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
