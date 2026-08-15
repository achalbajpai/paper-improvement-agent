"""The canonical sentence segmenter.

There is exactly one of these, and it is versioned. Claim extraction, anchor
creation, attachment re-verification, and citation insertion must all agree on
where sentences begin and end; two segmenters that disagree by one boundary
produce anchors that silently point at the wrong sentence. ``SEGMENTER_VERSION``
is stored on every anchor so a stored anchor is never compared across versions.

Citations are **atomised before segmentation**. A marker like ``(Smith, 2021.)``
or ``[1, p. 4.]`` contains sentence-ending punctuation, so segmenting the raw
prose would split a citation in half and attach the two pieces to different
sentences. Replacing each occurrence with ``[[CITE:cite_012]]`` -- which contains
no boundary punctuation -- makes that impossible rather than unlikely.

Pure: no I/O, no network, no database.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.document import CitationRef, Paragraph, TextRun
from app.domain.hashing import normalize_text
from app.domain.ids import sentence_id

SEGMENTER_VERSION = "seg/v1"

TOKEN_PREFIX = "[[CITE:"
TOKEN_SUFFIX = "]]"
TOKEN_PATTERN = re.compile(r"\[\[CITE:([A-Za-z0-9_]+)\]\]")

_TERMINATORS = ".!?"
_OPENERS = "([{"
_CLOSERS = ")]}"
_TRAILING = "\"'”’)]}"


_TITLE_ABBREVIATIONS = frozenset({"dr", "jr", "messrs", "mr", "mrs", "ms", "prof", "sr", "st"})


_ARGUMENT_ABBREVIATIONS = frozenset(
    {
        "art",
        "chap",
        "chaps",
        "col",
        "cols",
        "eq",
        "eqs",
        "fig",
        "figs",
        "no",
        "nos",
        "p",
        "pp",
        "ref",
        "refs",
        "sec",
        "secs",
        "tab",
        "tabs",
        "vol",
        "vols",
    }
)


_PHRASE_ABBREVIATIONS = frozenset(
    {
        "al",
        "approx",
        "ca",
        "cf",
        "e.g",
        "ed",
        "eds",
        "esp",
        "et",
        "etc",
        "i.e",
        "inc",
        "ltd",
        "resp",
        "viz",
        "vs",
    }
)


@dataclass(frozen=True)
class Sentence:
    id: str
    index: int

    text: str

    plain_text: str
    citation_ids: tuple[str, ...]
    char_start: int
    char_end: int


def tokenize(paragraph: Paragraph) -> str:
    """Render a paragraph with each citation occurrence as one atomic token."""
    parts: list[str] = []
    for node in paragraph.inlines:
        if isinstance(node, TextRun):
            parts.append(node.text)
        elif isinstance(node, CitationRef):
            parts.append(f"{TOKEN_PREFIX}{node.citation_id}{TOKEN_SUFFIX}")
    return "".join(parts)


def detokenize(text: str) -> str:
    """Strip citation tokens, leaving prose. Used for hashing and word counts."""
    return TOKEN_PATTERN.sub("", text)


def token_ids(text: str) -> tuple[str, ...]:
    return tuple(match.group(1) for match in TOKEN_PATTERN.finditer(text))


def segment_text(text: str) -> list[tuple[int, int]]:
    """Return (start, end) spans of sentences in an already-tokenised string."""
    spans: list[tuple[int, int]] = []
    depth = 0
    start = 0
    index = 0
    length = len(text)

    while index < length:
        char = text[index]

        if text.startswith(TOKEN_PREFIX, index):
            closing = text.find(TOKEN_SUFFIX, index)
            if closing != -1:
                index = closing + len(TOKEN_SUFFIX)
                continue

        if char in _OPENERS:
            depth += 1
        elif char in _CLOSERS:
            depth = max(0, depth - 1)
        elif char in _TERMINATORS and depth == 0 and _is_boundary(text, index):
            end = index + 1

            while end < length and text[end] in _TRAILING:
                end += 1
            if text[start:end].strip():
                spans.append((start, end))
            while end < length and text[end].isspace():
                end += 1
            start = end
            index = end
            continue

        index += 1

    if text[start:].strip():
        spans.append((start, length))
    return spans


def _is_boundary(text: str, index: int) -> bool:

    if text[index] == "." and _is_decimal_point(text, index):
        return False

    cursor = index + 1
    while cursor < len(text) and text[cursor] in _TRAILING:
        cursor += 1
    if cursor >= len(text):
        return not _ends_in_title(text, index)
    if not text[cursor].isspace():
        return False

    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    if cursor >= len(text):
        return not _ends_in_title(text, index)

    if text[index] == "." and not _abbreviation_allows_boundary(text, index, text[cursor]):
        return False

    if text.startswith(TOKEN_PREFIX, cursor):
        return True
    following = text[cursor]
    return following.isupper() or following.isdigit() or following in "\"'“‘([$"


def _abbreviation_allows_boundary(text: str, index: int, following: str) -> bool:
    """Whether the token before this period permits a sentence to end here."""
    word = _preceding_word(text, index).lower()
    if not word:
        return True
    if word in _TITLE_ABBREVIATIONS:
        return False
    if word in _ARGUMENT_ABBREVIATIONS and (following.isdigit() or following in "(["):
        return False
    if word in _PHRASE_ABBREVIATIONS and (following.isdigit() or following.islower()):
        return False

    return not (len(word) == 1 and word.isalpha())


def _ends_in_title(text: str, index: int) -> bool:
    """A trailing "Dr." at the end of a fragment is still not a sentence end."""
    return text[index] == "." and _preceding_word(text, index).lower() in _TITLE_ABBREVIATIONS


def _is_decimal_point(text: str, index: int) -> bool:
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    return before.isdigit() and after.isdigit()


def _preceding_word(text: str, index: int) -> str:
    cursor = index - 1
    while cursor >= 0 and (text[cursor].isalnum() or text[cursor] == "."):
        cursor -= 1
    return text[cursor + 1 : index]


def segment_paragraph(paragraph: Paragraph) -> tuple[Sentence, ...]:
    """Split one paragraph into anchored sentences."""
    tokenised = tokenize(paragraph)
    sentences: list[Sentence] = []
    for index, (start, end) in enumerate(segment_text(tokenised), start=1):
        fragment = tokenised[start:end]
        sentences.append(
            Sentence(
                id=sentence_id(paragraph.id, index),
                index=index,
                text=fragment,
                plain_text=normalize_text(detokenize(fragment)),
                citation_ids=token_ids(fragment),
                char_start=start,
                char_end=end,
            )
        )
    return tuple(sentences)


def segment_plain(text: str) -> tuple[str, ...]:
    """Split a plain string, for provider abstracts rather than manuscripts."""
    return tuple(
        normalize_text(text[start:end])
        for start, end in segment_text(text)
        if text[start:end].strip()
    )
