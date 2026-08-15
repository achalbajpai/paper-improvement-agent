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
    sentence_id: str

    offset: int
    citation_id: str
    reference_id: str

    merge_into: str | None = None


def insertion_offset(tokenised: str, sentence_start: int, sentence_end: int) -> int:
    index = sentence_end
    while index > sentence_start and tokenised[index - 1].isspace():
        index -= 1
    while index > sentence_start and tokenised[index - 1] in _TRAILING:
        index -= 1
    while index > sentence_start and tokenised[index - 1] in _TERMINATORS:
        index -= 1
    return index


def adjacent_citation(tokenised: str, offset: int) -> str | None:
    for match in TOKEN_PATTERN.finditer(tokenised):
        if match.end() == offset or (match.start() <= offset <= match.end()):
            return match.group(1)
        trailing = tokenised[match.end() : offset]
        if match.end() < offset and trailing.strip() == "":
            return match.group(1)
    return None


def apply_insertions(tokenised: str, insertions: list[Insertion]) -> str:
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
    if _prose_key(before) != _prose_key(after):
        raise ProseMutationError(
            "Adding a citation changed the paragraph's prose, which it must never do.",
            paragraph_id=paragraph_id,
        )


def _prose_key(text: str) -> str:
    stripped = detokenize(text)
    return _SPACE_BEFORE_PUNCTUATION.sub(r"\1", " ".join(stripped.split()))


def merged_node(existing: CitationNode, reference_id: str) -> CitationNode:
    mode = existing.items[0].mode if existing.items else CitationMode.NORMAL
    return existing.model_copy(
        update={"items": (*existing.items, CitationItem(reference_id=reference_id, mode=mode))}
    )


def new_node(citation_id: str, reference_id: str) -> CitationNode:
    return CitationNode(
        id=citation_id,
        items=(CitationItem(reference_id=reference_id, mode=CitationMode.NORMAL),),
        raw_marker="",
    )
