"""The segmenter's contract.

Every anchor in the system -- claim anchors, attachment re-verification, citation
insertion points -- is a (paragraph, sentence index) pair produced here. A
boundary that moves by one silently repoints an anchor at a neighbouring
sentence, and nothing downstream can detect it. These tests pin the boundaries.
"""

from __future__ import annotations

import pytest

from app.domain.document import CitationRef, Paragraph, TextRun
from app.services.parser.segmenter import (
    SEGMENTER_VERSION,
    detokenize,
    segment_paragraph,
    segment_plain,
    segment_text,
    token_ids,
    tokenize,
)
from tests.conftest import validated


def paragraph_of(*nodes: TextRun | CitationRef) -> Paragraph:
    return Paragraph(id="p_1_1", order_index=0, inlines=tuple(nodes))


def test_version_is_pinned() -> None:

    assert SEGMENTER_VERSION == "seg/v1"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("One sentence.", 1),
        ("First. Second.", 2),
        ("First! Second? Third.", 3),
        ("See Fig. 3 for detail. Then continue.", 2),
        ("Smith et al. reported this. We disagree.", 2),
        ("Use e.g. this approach. It works.", 2),
        ("Accuracy rose to 92.5 percent. That is high.", 2),
        ("J. Smith proposed it. We extend it.", 2),
        ('He said "no." Then he left.', 2),
        ("See Table no. 5 for detail. Then continue.", 2),
        ("Dr. Smith agreed. We did not.", 2),
        ("As shown in p. 14 this holds. It also generalises.", 2),
    ],
)
def test_boundaries(text: str, expected: int) -> None:
    assert len(segment_text(text)) == expected


def test_citation_token_is_never_split() -> None:
    """The reason citations are atomised before segmentation.

    A marker like ``[1, p. 4.]`` contains a period. Segmenting raw prose would
    cut the marker in half and attach the two pieces to different sentences.
    """
    paragraph = paragraph_of(
        TextRun(text="Transformers dominate "),
        CitationRef(citation_id="cite_imported_001"),
        TextRun(text=". They also scale."),
    )
    sentences = segment_paragraph(paragraph)
    assert len(sentences) == 2
    assert sentences[0].citation_ids == ("cite_imported_001",)
    assert sentences[1].citation_ids == ()


def test_tokenise_round_trips_prose() -> None:
    paragraph = paragraph_of(
        TextRun(text="Before "),
        CitationRef(citation_id="cite_imported_007"),
        TextRun(text=" after."),
    )
    tokenised = tokenize(paragraph)
    assert token_ids(tokenised) == ("cite_imported_007",)
    assert detokenize(tokenised) == "Before  after."


def test_sentence_ids_are_positional_and_stable() -> None:
    paragraph = paragraph_of(TextRun(text="One. Two. Three."))
    first = segment_paragraph(paragraph)
    second = segment_paragraph(paragraph)
    assert [s.id for s in first] == ["p_1_1:s1", "p_1_1:s2", "p_1_1:s3"]
    assert [s.id for s in first] == [s.id for s in second]


def test_plain_text_excludes_citations() -> None:
    paragraph = paragraph_of(
        TextRun(text="Attention works "),
        CitationRef(citation_id="cite_imported_002"),
        TextRun(text=" well."),
    )
    (sentence,) = segment_paragraph(paragraph)
    assert "CITE" not in sentence.plain_text
    assert sentence.plain_text == "Attention works well."


def test_empty_paragraph_yields_no_sentences() -> None:
    assert segment_paragraph(paragraph_of()) == ()
    assert segment_paragraph(paragraph_of(TextRun(text="   "))) == ()


def test_segment_plain_normalises_whitespace() -> None:
    assert segment_plain("First   line.\n\nSecond  line.") == ("First line.", "Second line.")


def test_every_corpus_paragraph_segments_without_loss(corpus_name: str) -> None:
    """No prose may disappear between the paragraph and its sentences.

    Segmentation is lossy only in whitespace. If a boundary rule ever drops a
    fragment -- a trailing clause after an unmatched bracket, say -- claim
    extraction would silently never see that text.
    """
    document = validated(corpus_name).document
    for paragraph in document.paragraphs():
        sentences = segment_paragraph(paragraph)
        rejoined = "".join(sentence.text for sentence in sentences)
        assert "".join(rejoined.split()) == "".join(tokenize(paragraph).split())


def test_every_corpus_citation_lands_in_exactly_one_sentence(corpus_name: str) -> None:
    document = validated(corpus_name).document
    for paragraph in document.paragraphs():
        placed = [
            citation
            for sentence in segment_paragraph(paragraph)
            for citation in sentence.citation_ids
        ]
        assert placed == list(paragraph.citation_ids)
        assert len(placed) == len(set(placed))
