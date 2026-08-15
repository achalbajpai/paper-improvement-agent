from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.domain.hashing import normalize_text
from app.domain.ids import Origin, origin_of


class CitationMode(StrEnum):
    NORMAL = "NORMAL"

    AUTHOR_IN_TEXT = "AUTHOR_IN_TEXT"

    SUPPRESS_AUTHOR = "SUPPRESS_AUTHOR"


class SemanticParseStatus(StrEnum):
    STRUCTURED = "STRUCTURED"

    PARTIAL_MODIFIERS = "PARTIAL_MODIFIERS"

    RAW_ONLY = "RAW_ONLY"


class LocatorLabel(StrEnum):
    PAGE = "page"
    CHAPTER = "chapter"
    SECTION = "section"
    FIGURE = "figure"
    TABLE = "table"
    EQUATION = "equation"


class CitationItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    reference_id: str | None = None
    mode: CitationMode = CitationMode.NORMAL
    prefix: str = ""
    suffix: str = ""
    locator: str = ""
    locator_label: LocatorLabel | None = None

    @property
    def is_linked(self) -> bool:
        return self.reference_id is not None

    def encoded_suffix(self) -> str:
        parts: list[str] = []
        if self.locator:
            label = self.locator_label.value if self.locator_label else "page"
            abbreviation = {
                "page": "p.",
                "chapter": "chap.",
                "section": "sec.",
                "figure": "fig.",
                "table": "tbl.",
                "equation": "eq.",
            }[label]
            parts.append(f"{abbreviation} {self.locator}")
        if self.suffix:
            parts.append(self.suffix.lstrip(", "))
        if not parts:
            return ""
        return ", " + ", ".join(parts)

    def signature(self) -> str:
        return "|".join(
            [
                self.reference_id or "",
                self.mode.value,
                normalize_text(self.prefix),
                normalize_text(self.encoded_suffix()),
            ]
        )


class CitationNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    items: tuple[CitationItem, ...] = Field(default_factory=tuple)

    raw_marker: str = ""
    semantic_parse_status: SemanticParseStatus = SemanticParseStatus.STRUCTURED

    @computed_field
    @property
    def fidelity_exportable(self) -> bool:
        return self.semantic_parse_status is SemanticParseStatus.STRUCTURED

    @property
    def origin(self) -> Origin | None:
        return origin_of(self.id)

    @property
    def reference_ids(self) -> tuple[str, ...]:
        return tuple(item.reference_id for item in self.items if item.reference_id is not None)

    @property
    def is_unlinked(self) -> bool:
        return not any(item.is_linked for item in self.items)

    def signature(self) -> str:
        return f"{self.id}::" + ";".join(item.signature() for item in self.items)
