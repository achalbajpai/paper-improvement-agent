"""Where a new citation marker goes, decided by code rather than by a model.

ADD_SUPPORTING_CITATIONS regenerates no prose. The model chooses *which* work
supports *which* sentence; this module puts the marker in the sentence, and the
only thing that changes in the paragraph is that one token appears.

That constraint is what makes the intent safe to run unattended. If a model
produced the revised paragraph, every sentence in it would need re-verifying for
invented content. Because the mutation is a single token insertion into an
otherwise untouched string, the prose is provably unchanged: the check is
character equality after removing the inserted token.

The placement rule is the convention a reader expects: immediately before the
sentence's terminal punctuation, after any closing quote or bracket. Where a
citation already sits at that position, the new item joins the existing
occurrence instead of opening a second bracket beside it -- ``[3, 7]`` rather
than ``[3][7]``.

Pure: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.citation import CitationItem, CitationMode, CitationNode
from app.domain.errors import ProseMutationError
from app.services.parser.segmenter import TOKEN_PATTERN, detokenize

_TERMINATORS = ".!?"
_TRAILING = "\"'”’)]}"
_SPACE_BEFORE_PUNCTUATION = re.compile(r"\s+([.,;:!?)\]}])")


@dataclass(frozen=True)
class Insertion:
    """One marker to place, resolved to a character offset."""

    sentence_id: str

    offset: int
    citation_id: str
    reference_id: str

    merge_into: str | None = None


def insertion_offset(tokenised: str, sentence_start: int, sentence_end: int) -> int:
    """Where in this sentence a new marker belongs.

    Before the terminal punctuation and any trailing quote or bracket, so
    ``...as shown."`` becomes ``...as shown [1]."`` rather than
    ``...as shown." [1]``.
    """
    index = sentence_end
    while index > sentence_start and tokenised[index - 1].isspace():
        index -= 1
    while index > sentence_start and tokenised[index - 1] in _TRAILING:
        index -= 1
    while index > sentence_start and tokenised[index - 1] in _TERMINATORS:
        index -= 1
    return index


def adjacent_citation(tokenised: str, offset: int) -> str | None:
    """The occurrence already sitting at this insertion point, if any.

    A second bracket beside an existing one is not how anybody writes citations,
    so the new item joins that occurrence instead.
    """
    for match in TOKEN_PATTERN.finditer(tokenised):
        if match.end() == offset or (match.start() <= offset <= match.end()):
            return match.group(1)
        trailing = tokenised[match.end() : offset]
        if match.end() < offset and trailing.strip() == "":
            return match.group(1)
    return None


def apply_insertions(tokenised: str, insertions: list[Insertion]) -> str:
    """Insert every non-merging marker, right to left so offsets stay valid."""
    text = tokenised
    for insertion in sorted(insertions, key=lambda item: -item.offset):
        if insertion.merge_into is not None:
            continue
        token = f"[[CITE:{insertion.citation_id}]]"
        prefix = text[: insertion.offset].rstrip()
        separator = " " if prefix else ""
        text = f"{prefix}{separator}{token}{text[insertion.offset :]}"
    return text


def assert_prose_unchanged(before: str, after: str, paragraph_id: str) -> None:
    """The guarantee this intent rests on.

    Comparing the two paragraphs with all citation tokens stripped leaves only
    prose, which must be identical to the whitespace. A typed error rather than
    an assertion: ``python -O`` strips assertions, and this is a safety boundary.
    """
    if _prose_key(before) != _prose_key(after):
        raise ProseMutationError(
            "Adding a citation changed the paragraph's prose, which it must never do.",
            paragraph_id=paragraph_id,
        )


def _prose_key(text: str) -> str:
    """The paragraph's prose, with citation markers and their spacing removed.

    Removing a marker from ``as shown [[CITE:c1]].`` leaves ``as shown .``, so the
    space a marker leaves behind is closed up before comparing. Everything else is
    compared as written: a single changed word still fails.
    """
    stripped = detokenize(text)
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", " ".join(stripped.split()))


def merged_node(existing: CitationNode, reference_id: str) -> CitationNode:
    """Add one item to an occurrence that is already in the sentence.

    The new item inherits the existing occurrence's mode, because both render
    inside one bracket and a mixed-mode bracket is not a thing citeproc can
    produce.
    """
    mode = existing.items[0].mode if existing.items else CitationMode.NORMAL
    return existing.model_copy(
        update={"items": (*existing.items, CitationItem(reference_id=reference_id, mode=mode))}
    )


def new_node(citation_id: str, reference_id: str) -> CitationNode:
    """A fresh occurrence for an added citation.

    Always ``NORMAL``: an author-in-text marker would require the surrounding
    sentence to name the author, and this intent does not write sentences.
    """
    return CitationNode(
        id=citation_id,
        items=(CitationItem(reference_id=reference_id, mode=CitationMode.NORMAL),),
        raw_marker="",
    )
