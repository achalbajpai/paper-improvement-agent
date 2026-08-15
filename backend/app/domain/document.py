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
    model_config = ConfigDict(frozen=True)
    kind: Literal["citation"] = "citation"
    citation_id: str


InlineNode = TextRun | CitationRef


class Author(BaseModel):
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
        joined = "".join(node.text for node in self.inlines if isinstance(node, TextRun))
        return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", joined)

    @property
    def citation_ids(self) -> tuple[str, ...]:
        return tuple(node.citation_id for node in self.inlines if isinstance(node, CitationRef))

    def word_count(self) -> int:
        return len(normalize_text(self.text).split())


class BlockRef(BaseModel):
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
        return tuple(node.id for node in self.citations.values() if not node.fidelity_exportable)

    def citations_by_parse_status(self, status: SemanticParseStatus) -> tuple[str, ...]:
        return tuple(
            node.id for node in self.citations.values() if node.semantic_parse_status is status
        )

    def content_hash(self) -> str:
        return canonical_sha256(self)
