"""Splitting a snapshotted abstract into addressable evidence spans.

Evidence is offered to the model as ``span_id: text`` and comes back as span ids
alone. The server then builds the ``EvidenceAnchor`` from its own offsets, which
is what makes "the model quoted something the source does not say" impossible
rather than merely unlikely.

Offsets are computed against the exact string stored in ``source_records``. The
canonical segmenter is reused rather than reimplemented, so an abstract and a
manuscript paragraph are split by the same rules; a second splitter would drift
from the first and silently move every anchor it produced.

Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.hashing import text_sha256
from app.domain.review import EvidenceAnchor
from app.services.parser.segmenter import segment_text

MAX_SPANS = 24


@dataclass(frozen=True)
class EvidenceSpan:
    """One addressable region of a snapshotted abstract."""

    id: str
    text: str
    char_start: int
    char_end: int

    def anchor(self, source_record_id: str) -> EvidenceAnchor:
        return EvidenceAnchor(
            source_record_id=source_record_id,
            span_id=self.id,
            char_start=self.char_start,
            char_end=self.char_end,
            text_hash=text_sha256(self.text),
        )


def split_abstract(abstract: str, *, max_spans: int = MAX_SPANS) -> tuple[EvidenceSpan, ...]:
    """Split an abstract into sentence spans with exact offsets into it."""
    if not abstract.strip():
        return ()
    spans: list[EvidenceSpan] = []
    for index, (start, end) in enumerate(segment_text(abstract)):
        text = abstract[start:end].strip()
        if not text:
            continue
        spans.append(
            EvidenceSpan(id=f"span_{index:03d}", text=text, char_start=start, char_end=end)
        )
        if len(spans) >= max_spans:
            break
    return tuple(spans)


def span_listing(spans: tuple[EvidenceSpan, ...]) -> list[tuple[str, str]]:
    """The (id, text) pairs a prompt shows the model."""
    return [(span.id, span.text) for span in spans]
