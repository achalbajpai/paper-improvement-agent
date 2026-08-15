"""The sentence a citation is attached to.

Attachment is checked when a citation's *context* changed, not when the citation
moved. A marker that slid to a different character offset because an earlier
sentence was shortened is still supporting the same claim; a marker that stayed
put while its sentence was reworded is not. Position is the thing that looks like
it matters and is not.

Context is keyed on occurrence identity rather than on sentence index, so it
survives sentences being added or removed around it.

The citation-only fragment case is the one worth being careful about. A sentence
that is nothing but markers -- ``[3, 7].`` on its own after a list -- carries no
claim, so the claim it supports is in the sentence before it. Treating the
fragment as the context would compare two empty strings and report, confidently,
that nothing changed.

Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.document import Document, Paragraph
from app.domain.hashing import text_sha256
from app.services.parser.segmenter import Sentence, segment_paragraph

MIN_SUBSTANTIVE_WORDS = 3


@dataclass(frozen=True)
class CitationContext:
    """What one occurrence is attached to, and enough to tell if it changed."""

    citation_id: str
    paragraph_id: str

    host_text: str
    previous_text: str = ""
    next_text: str = ""

    @property
    def host_hash(self) -> str:
        """Whitespace-insensitive, so a reflow is not mistaken for a rewording."""
        return text_sha256(self.host_text)


def contexts_for(document: Document) -> dict[str, CitationContext]:
    """Every placed occurrence in the document, keyed by occurrence id."""
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
    """Walk back past citation-only fragments to the sentence making the claim."""
    cursor = index
    while cursor > 0 and _is_fragment(sentences[cursor]):
        cursor -= 1
    return cursor


def _is_fragment(sentence: Sentence) -> bool:
    return len(sentence.plain_text.split()) < MIN_SUBSTANTIVE_WORDS


def changed_contexts(
    before: dict[str, CitationContext], after: dict[str, CitationContext]
) -> tuple[str, ...]:
    """Occurrences that survived the edit and whose claim was reworded.

    Movement alone is not a change: the comparison is on the host sentence's
    text, whitespace-insensitively, so reflowing a paragraph re-verifies nothing
    and rewording one sentence re-verifies exactly the citations in it.
    """
    return tuple(
        sorted(
            citation_id
            for citation_id, context in after.items()
            if citation_id in before and before[citation_id].host_hash != context.host_hash
        )
    )
