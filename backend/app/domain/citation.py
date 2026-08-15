"""The citation occurrence model.

A citation in a manuscript is not a string. ``[12]`` and ``(Smith, 2021)`` are
*renderings* of the same underlying thing, and the same rendering can mean
different things in different styles. The occurrence therefore stores structure,
and the marker the reader saw is kept verbatim beside it.

The pivotal field is ``semantic_parse_status``. A parser that cannot fully
understand a marker must say so rather than guess, because a confidently wrong
citation is worse than an admittedly uncertain one.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.domain.hashing import normalize_text
from app.domain.ids import Origin, origin_of


class CitationMode(StrEnum):
    """How the marker sits in the sentence.

    These map one-to-one onto Pandoc's citation modes.
    """

    NORMAL = "NORMAL"

    AUTHOR_IN_TEXT = "AUTHOR_IN_TEXT"

    SUPPRESS_AUTHOR = "SUPPRESS_AUTHOR"


class SemanticParseStatus(StrEnum):
    """How much of the marker the parser actually understood."""

    STRUCTURED = "STRUCTURED"

    PARTIAL_MODIFIERS = "PARTIAL_MODIFIERS"

    RAW_ONLY = "RAW_ONLY"


class LocatorLabel(StrEnum):
    """CSL locator labels, restricted to the ones this system emits."""

    PAGE = "page"
    CHAPTER = "chapter"
    SECTION = "section"
    FIGURE = "figure"
    TABLE = "table"
    EQUATION = "equation"


class CitationItem(BaseModel):
    """One reference inside one occurrence.

    ``[2, 5]`` is a single occurrence carrying two items. Modelling it as two
    occurrences would lose the fact that they render as one bracket, and
    modelling it as one item would lose which references were cited.
    """

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
        """The suffix exactly as it will be handed to citeproc.

        Citeproc *rewrites* locators on output --
        ``pp. 17-19`` renders as ``pp. 17–19``, ``chap. 3`` as ``Ch. 3``. The
        rendered form is therefore never parsed back, and export equality
        compares this encoded input form instead.
        """
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
        """Whole-item identity for export comparison.

        Compared as a whole item on the encoded form, never as a set of keys: two
        items that agree on reference and locator but differ in mode are not the
        same citation.
        """
        return "|".join(
            [
                self.reference_id or "",
                self.mode.value,
                normalize_text(self.prefix),
                normalize_text(self.encoded_suffix()),
            ]
        )


class CitationNode(BaseModel):
    """One marker as it appears in the text."""

    model_config = ConfigDict(frozen=True)

    id: str
    items: tuple[CitationItem, ...] = Field(default_factory=tuple)

    raw_marker: str = ""
    semantic_parse_status: SemanticParseStatus = SemanticParseStatus.STRUCTURED

    @computed_field
    @property
    def fidelity_exportable(self) -> bool:
        """Whether this occurrence can be re-rendered without losing information.

        Derived rather than stored, so it cannot drift out of step with
        ``semantic_parse_status``. A PARTIAL_MODIFIERS occurrence is not
        exportable at fidelity: re-rendering it would drop the modifier the
        parser saw but could not represent, which is a silent change to the
        author's meaning.
        """
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
        """Whole-occurrence identity, used by the export equality check."""
        return f"{self.id}::" + ";".join(item.signature() for item in self.items)
