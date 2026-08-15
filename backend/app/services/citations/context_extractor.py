from __future__ import annotations

from dataclasses import dataclass

from app.domain.hashing import text_sha256
from app.domain.review import EvidenceAnchor
from app.services.parser.segmenter import segment_text

MAX_SPANS = 24


@dataclass(frozen=True)
class EvidenceSpan:
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
    return [(span.id, span.text) for span in spans]
