from __future__ import annotations

from app.domain.citation import CitationNode
from app.domain.document import (
    BodyNode,
    CitationRef,
    Document,
    InlineNode,
    Paragraph,
    Section,
    TextRun,
)
from app.domain.errors import CandidateConstructionError
from app.domain.reference import ReferenceRecord
from app.services.parser.segmenter import TOKEN_PATTERN


class CandidateRevisionBuilder:
    def __init__(self, base: Document) -> None:
        self.base = base
        self._paragraphs: dict[str, tuple[InlineNode, ...]] = {}
        self._citations: dict[str, CitationNode] = {}
        self._references: list[ReferenceRecord] = []

    def replace_paragraph(self, paragraph_id: str, tokenised_text: str) -> None:
        paragraph = self.base.paragraph(paragraph_id)
        if paragraph is None:
            raise CandidateConstructionError(
                "The edit targeted a paragraph that is not in this document.",
                paragraph_id=paragraph_id,
            )
        self._paragraphs[paragraph_id] = inlines_from_tokenised(
            tokenised_text, known=self._known_citation_ids()
        )

    def add_citation(self, node: CitationNode) -> None:
        if node.id in self.base.citations or node.id in self._citations:
            raise CandidateConstructionError(
                "A citation occurrence with this id already exists.", citation_id=node.id
            )
        self._citations[node.id] = node

    def replace_citation(self, node: CitationNode) -> None:
        if node.id not in self.base.citations:
            raise CandidateConstructionError(
                "The edit replaced a citation occurrence that is not in this document.",
                citation_id=node.id,
            )
        self._citations[node.id] = node

    def add_reference(self, record: ReferenceRecord) -> None:
        if self.base.reference(record.id) is not None:
            raise CandidateConstructionError(
                "A reference with this id already exists.", reference_id=record.id
            )
        self._references.append(record)

    def build(self) -> Document:
        if not self._paragraphs and not self._citations and not self._references:
            raise CandidateConstructionError("The edit produced no changes to apply.")

        placed = self._placed_citation_ids()
        orphans = sorted(
            citation_id for citation_id in self._citations if citation_id not in placed
        )
        if orphans:
            raise CandidateConstructionError(
                "A new citation was registered but never placed in a paragraph.",
                citation_ids=orphans,
            )

        citations = {
            citation_id: node
            for citation_id, node in {**self.base.citations, **self._citations}.items()
            if citation_id in placed
        }

        return self.base.model_copy(
            update={
                "sections": tuple(self._rebuild(section) for section in self.base.sections),
                "citations": citations,
                "references": (*self.base.references, *self._references),
            }
        )

    def _rebuild(self, section: Section) -> Section:
        body: list[BodyNode] = []
        for node in section.body:
            if isinstance(node, Paragraph) and node.id in self._paragraphs:
                body.append(node.model_copy(update={"inlines": self._paragraphs[node.id]}))
            else:
                body.append(node)
        return section.model_copy(update={"body": tuple(body)})

    def _known_citation_ids(self) -> frozenset[str]:
        return frozenset(self.base.citations) | frozenset(self._citations)

    def _placed_citation_ids(self) -> set[str]:
        placed: set[str] = set()
        for section in self.base.sections:
            for paragraph in section.paragraphs:
                inlines = self._paragraphs.get(paragraph.id)
                if inlines is None:
                    placed.update(paragraph.citation_ids)
                else:
                    placed.update(
                        node.citation_id for node in inlines if isinstance(node, CitationRef)
                    )
        return placed


def inlines_from_tokenised(text: str, *, known: frozenset[str]) -> tuple[InlineNode, ...]:
    inlines: list[InlineNode] = []
    cursor = 0
    for match in TOKEN_PATTERN.finditer(text):
        prose = text[cursor : match.start()]
        if prose:
            inlines.append(TextRun(text=prose))
        citation_id = match.group(1)
        if citation_id not in known:
            raise CandidateConstructionError(
                "The revised text referenced a citation that does not exist.",
                citation_id=citation_id,
            )
        inlines.append(CitationRef(citation_id=citation_id))
        cursor = match.end()

    tail = text[cursor:]
    if tail:
        inlines.append(TextRun(text=tail))
    return tuple(inlines)
