from __future__ import annotations

from enum import StrEnum


class PaperStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PARSING = "PARSING"
    PARSED = "PARSED"
    PARSE_FAILED = "PARSE_FAILED"
    DELETING = "DELETING"


class RunStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CitationStyle(StrEnum):
    IEEE = "IEEE"
    APA = "APA"


class StyleConfidence(StrEnum):
    HIGH = "HIGH"

    LOW = "LOW"

    UNKNOWN = "UNKNOWN"
