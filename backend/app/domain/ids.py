from __future__ import annotations

import re
from enum import StrEnum

SECTION = "sec_{ordinal}"
PARAGRAPH = "p_{section}_{ordinal}"
BLOCK = "blk_{kind}_{ordinal:03d}"
SENTENCE = "{paragraph_id}:s{index}"

CITATION_IMPORTED = "cite_imported_{ordinal:03d}"
CITATION_ADDED = "cite_added_{ordinal:03d}"
REFERENCE_IMPORTED = "ref_imported_{ordinal:03d}"
REFERENCE_ADDED = "ref_added_{ordinal:03d}"

SOURCE_RECORD = "src_{ordinal:03d}"

_SENTENCE_PATTERN = re.compile(r"^(?P<paragraph>p_\d+_\d+):s(?P<index>\d+)$")


class Origin(StrEnum):
    IMPORTED = "IMPORTED"
    ADDED = "ADDED"


def section_id(ordinal: int) -> str:
    return SECTION.format(ordinal=ordinal)


def paragraph_id(section_ordinal: int, paragraph_ordinal: int) -> str:
    return PARAGRAPH.format(section=section_ordinal, ordinal=paragraph_ordinal)


def block_id(kind: str, ordinal: int) -> str:
    return BLOCK.format(kind=kind.lower(), ordinal=ordinal)


def sentence_id(paragraph: str, index: int) -> str:
    return SENTENCE.format(paragraph_id=paragraph, index=index)


def parse_sentence_id(value: str) -> tuple[str, int] | None:
    match = _SENTENCE_PATTERN.match(value)
    if not match:
        return None
    return match.group("paragraph"), int(match.group("index"))


def citation_id(origin: Origin, ordinal: int) -> str:
    template = CITATION_IMPORTED if origin is Origin.IMPORTED else CITATION_ADDED
    return template.format(ordinal=ordinal)


def reference_id(origin: Origin, ordinal: int) -> str:
    template = REFERENCE_IMPORTED if origin is Origin.IMPORTED else REFERENCE_ADDED
    return template.format(ordinal=ordinal)


def source_record_id(ordinal: int) -> str:
    return SOURCE_RECORD.format(ordinal=ordinal)


def origin_of(identifier: str) -> Origin | None:
    if "_imported_" in identifier:
        return Origin.IMPORTED
    if "_added_" in identifier:
        return Origin.ADDED
    return None
