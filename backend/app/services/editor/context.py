from __future__ import annotations

from dataclasses import dataclass

from app.domain.document import Document, Paragraph
from app.domain.hashing import text_sha256
from app.services.parser.segmenter import Sentence, segment_paragraph

MIN_SUBSTANTIVE_WORDS = 3


@dataclass(frozen=True)
class CitationContext:
    citation_id: str
    paragraph_id: str

    host_text: str
    previous_text: str = ""
    next_text: str = ""

    @property
    def host_hash(self) -> str:
        return text_sha256(self.host_text)


def contexts_for(document: Document) -> dict[str, CitationContext]:
    found: dict[str, CitationContext] = {}
    for paragraph in document.paragraphs():
        found.update(paragraph_contexts(paragraph))
    return found


def paragraph_contexts(paragraph: Paragraph) -> dict[str, CitationContext]:
    sentences = segment_paragraph(paragraph)
    found: dict[str, CitationContext] = {}
    for index, sentence in enumerate(sentences):
        for citation_id in sentence.citation_ids:
            host = _host_index(sentences, index)
            found[citation_id] = CitationContext(
                citation_id=citation_id,
                paragraph_id=paragraph.id,
                host_text=sentences[host].plain_text,
                previous_text=sentences[host - 1].plain_text if host > 0 else "",
                next_text=(sentences[host + 1].plain_text if host + 1 < len(sentences) else ""),
            )
    return found


def _host_index(sentences: tuple[Sentence, ...], index: int) -> int:
    cursor = index
    while cursor > 0 and _is_fragment(sentences[cursor]):
        cursor -= 1
    return cursor


def _is_fragment(sentence: Sentence) -> bool:
    return len(sentence.plain_text.split()) < MIN_SUBSTANTIVE_WORDS


def changed_contexts(
    before: dict[str, CitationContext], after: dict[str, CitationContext]
) -> tuple[str, ...]:
    return tuple(
        sorted(
            citation_id
            for citation_id, context in after.items()
            if citation_id in before and before[citation_id].host_hash != context.host_hash
        )
    )
