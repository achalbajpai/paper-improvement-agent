"""Lifecycle vocabulary shared by the services and the API.

These enums started in ``api/schemas.py`` because that is where they are
serialised, but the parse pipeline and the acceptance transaction reason about
them too, and a service importing from ``api/`` would invert the dependency. They
live here and are re-exported for the wire.
"""

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
