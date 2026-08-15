"""Citation-style detection.

Detection **suggests**; the researcher decides through
``PATCH /papers/{id}/citation-style``. That split is deliberate. A paper's markers
narrow the family to numeric or author-year, but they do not identify the style:
IEEE, Vancouver, and Nature all print bracketed numerals, and APA, Chicago
author-date, and Harvard all print author-year. Silently exporting in the wrong
one of those would reformat every entry in the author's bibliography.

So this returns the best-supported of the two vendored styles together with an
honest confidence, and the UI asks.

Pure: reads a parsed document, does no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.citation import SemanticParseStatus
from app.domain.document import Document
from app.domain.lifecycle import CitationStyle, StyleConfidence

_BRACKETED_NUMERIC = re.compile(r"^\[\s*\d+")
_PARENTHESISED_AUTHOR_YEAR = re.compile(r"^\(?\s*[A-Z][\w'\u2019-]+.*\b(1[6-9]\d{2}|20\d{2})")


_MIN_EVIDENCE = 5
_STRONG_MAJORITY = 0.8


@dataclass(frozen=True)
class StyleDetection:
    style: CitationStyle | None
    confidence: StyleConfidence
    numeric_markers: int
    author_year_markers: int
    reason: str


def detect_style(document: Document) -> StyleDetection:
    numeric = 0
    author_year = 0

    for node in document.citations.values():
        if node.semantic_parse_status is SemanticParseStatus.RAW_ONLY and not node.reference_ids:
            continue
        marker = node.raw_marker.strip()
        if not marker:
            continue
        if _BRACKETED_NUMERIC.match(marker):
            numeric += 1
        elif _PARENTHESISED_AUTHOR_YEAR.match(marker):
            author_year += 1

    total = numeric + author_year
    if total < _MIN_EVIDENCE:
        return StyleDetection(
            style=None,
            confidence=StyleConfidence.UNKNOWN,
            numeric_markers=numeric,
            author_year_markers=author_year,
            reason="Too few recognisable markers to infer a style.",
        )

    if numeric / total >= _STRONG_MAJORITY:
        return StyleDetection(
            style=CitationStyle.IEEE,
            confidence=StyleConfidence.HIGH,
            numeric_markers=numeric,
            author_year_markers=author_year,
            reason=(
                f"{numeric} of {total} markers are bracketed numerals. IEEE is the "
                "numeric style available here; Vancouver and Nature look the same."
            ),
        )
    if author_year / total >= _STRONG_MAJORITY:
        return StyleDetection(
            style=CitationStyle.APA,
            confidence=StyleConfidence.HIGH,
            numeric_markers=numeric,
            author_year_markers=author_year,
            reason=(
                f"{author_year} of {total} markers are author-year. APA is the "
                "author-year style available here; Chicago and Harvard look similar."
            ),
        )

    leader = CitationStyle.IEEE if numeric >= author_year else CitationStyle.APA
    return StyleDetection(
        style=leader,
        confidence=StyleConfidence.LOW,
        numeric_markers=numeric,
        author_year_markers=author_year,
        reason=(
            f"Markers are mixed ({numeric} numeric, {author_year} author-year), so "
            "the leading family is a weak signal."
        ),
    )
