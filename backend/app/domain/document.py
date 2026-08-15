"""The manuscript AST.

Hybrid by design: prose is structured down to the inline level so it can be
edited and diffed, while non-prose blocks are preserved whole and referenced by
id. Trying to model a table's semantics would be a project of its own and would
buy nothing, since no supported edit modifies one.

Citations and blocks live in registries keyed by id rather than inline. That is
what makes protected-token substitution, delta computation, and the export
equality check operate on identity instead of on position in a string.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.block import PreservedBlock
from app.domain.citation import CitationNode, SemanticParseStatus
from app.domain.hashing import canonical_sha256, normalize_text
from app.domain.reference import ReferenceRecord


class TextRun(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["text"] = "text"
    text: str


class CitationRef(BaseModel):
    """A pointer into ``Document.citations``. The occurrence itself lives there."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["citation"] = "citation"
    citation_id: str


InlineNode = TextRun | CitationRef


class Author(BaseModel):
    """One manuscript author recovered from the paper header.

    GROBID exposes authors as structured ``persName`` elements. Keeping the
    display name in the document snapshot means export does not have to infer
    authors from the bibliography (which would be both incomplete and unsafe).
    """

    model_config = ConfigDict(frozen=True)

    name: str
    affiliations: tuple[str, ...] = Field(default_factory=tuple)


_SPACE_BEFORE_PUNCTUATION = re.compile(r"[ \t]+([.,;:!?)\]}])")


class Paragraph(BaseModel):
    model_config = ConfigDict(frozen=True)
    kind: Literal["paragraph"] = "paragraph"
    id: str
    order_index: int
    inlines: tuple[InlineNode, ...] = Field(default_factory=tuple)

    @property
    def text(self) -> str:
        """Prose only, with citation markers omitted.

        This is what a segmenter, a claim extractor, or a word count sees.
        Citations are atomised separately so a marker can never be split across
        two sentences.

        Removing a marker leaves the space that sat before it, so ``as shown [1].``
        would otherwise read as ``as shown .`` -- which counts as an extra word,
        and makes adding a citation look like a prose change in the diff even
        though not one character of prose moved. The gap is closed here, at the
        single point where prose is defined, rather than by every caller
        remembering to.
        """
        joined = "".join(node.text for node in self.inlines if isinstance(node, TextRun))
        return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", joined)

    @property
    def citation_ids(self) -> tuple[str, ...]:
        return tuple(node.citation_id for node in self.inlines if isinstance(node, CitationRef))

    def word_count(self) -> int:
        return len(normalize_text(self.text).split())


class BlockRef(BaseModel):
    """A pointer into ``Document.blocks``, holding the block's position in flow."""

    model_config = ConfigDict(frozen=True)
    kind: Literal["block"] = "block"
    block_id: str
    order_index: int


BodyNode = Paragraph | BlockRef


class Section(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: str
    title: str
    level: int = 1
    order_index: int = 0
    body: tuple[BodyNode, ...] = Field(default_factory=tuple)

    @property
    def paragraphs(self) -> tuple[Paragraph, ...]:
        return tuple(node for node in self.body if isinstance(node, Paragraph))

    def word_count(self) -> int:
        return sum(paragraph.word_count() for paragraph in self.paragraphs)


class Document(BaseModel):
    """A complete manuscript snapshot.

    Immutable. An edit produces a new Document; it never mutates this one, which
    is what lets the DeltaEngine diff before against after independently of
    whatever the editing code believed it did.
    """

    model_config = ConfigDict(frozen=True)

    title: str = ""
    authors: tuple[Author, ...] = Field(default_factory=tuple)
    abstract: str = ""
    sections: tuple[Section, ...] = Field(default_factory=tuple)
    citations: dict[str, CitationNode] = Field(default_factory=dict)
    blocks: dict[str, PreservedBlock] = Field(default_factory=dict)
    references: tuple[ReferenceRecord, ...] = Field(default_factory=tuple)

    floating_block_ids: tuple[str, ...] = Field(default_factory=tuple)

    segmenter_version: str = ""

    def paragraphs(self) -> tuple[Paragraph, ...]:
        return tuple(paragraph for section in self.sections for paragraph in section.paragraphs)

    def paragraph(self, paragraph_id: str) -> Paragraph | None:
        for paragraph in self.paragraphs():
            if paragraph.id == paragraph_id:
                return paragraph
        return None

    def section(self, section_id: str) -> Section | None:
        for section in self.sections:
            if section.id == section_id:
                return section
        return None

    def section_of(self, paragraph_id: str) -> Section | None:
        for section in self.sections:
            if any(paragraph.id == paragraph_id for paragraph in section.paragraphs):
                return section
        return None

    def reference(self, reference_id: str) -> ReferenceRecord | None:
        for reference in self.references:
            if reference.id == reference_id:
                return reference
        return None

    def word_count(self) -> int:
        return sum(section.word_count() for section in self.sections)

    def cited_reference_ids(self) -> set[str]:
        """References with at least one live occurrence.

        The complement is what targeted ``nocite`` has to retain so that an edit
        which removes the last citation of a work does not delete that work from
        the author's bibliography.
        """
        cited: set[str] = set()
        for paragraph in self.paragraphs():
            for citation_id in paragraph.citation_ids:
                node = self.citations.get(citation_id)
                if node:
                    cited.update(node.reference_ids)
        return cited

    def unrenderable_block_ids(self) -> tuple[str, ...]:
        return tuple(block.id for block in self.blocks.values() if block.blocks_fidelity_export)

    def non_exportable_citation_ids(self) -> tuple[str, ...]:
        """Markers that cannot be re-rendered without losing something.

        Two distinct causes, kept apart by ``citations_by_parse_status`` because
        they are different losses: a marker whose modifiers were only partly
        parsed may drop a page number, while a raw-only marker has no structure
        at all and renders as the literal text the author wrote. Reporting them
        under one label would misdescribe whichever one is not the majority.
        """
        return tuple(node.id for node in self.citations.values() if not node.fidelity_exportable)

    def citations_by_parse_status(self, status: SemanticParseStatus) -> tuple[str, ...]:
        return tuple(
            node.id for node in self.citations.values() if node.semantic_parse_status is status
        )

    def content_hash(self) -> str:
        """Identity of this snapshot's content.

        Covers prose, citation structure, blocks, and references together,
        because an edit that changed only a citation's locator has changed the
        manuscript just as surely as one that reworded a sentence.
        """
        return canonical_sha256(self)
