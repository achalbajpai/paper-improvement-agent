from __future__ import annotations

from dataclasses import dataclass

from app.domain.document import Paragraph, Section
from app.domain.errors import CandidateConstructionError

MIN_PARAGRAPH_WORDS = 40


MIN_ABSOLUTE_REDUCTION = 10

DEFAULT_REDUCTION_RATIO = 0.25


@dataclass(frozen=True)
class ParagraphTarget:
    paragraph_id: str
    words_before: int
    target_words: int

    @property
    def reduction(self) -> int:
        return self.words_before - self.target_words


@dataclass(frozen=True)
class ShortenPlan:
    section_id: str
    ratio: float
    targets: tuple[ParagraphTarget, ...]

    skipped: tuple[tuple[str, str], ...] = ()

    @property
    def words_before(self) -> int:
        return sum(target.words_before for target in self.targets)

    @property
    def words_target(self) -> int:
        return sum(target.target_words for target in self.targets)


def plan_shortening(
    section: Section, ratio: float | None = None, paragraph_id: str | None = None
) -> ShortenPlan:
    effective = _clamp(ratio if ratio is not None else DEFAULT_REDUCTION_RATIO)

    in_scope = [
        paragraph
        for paragraph in section.paragraphs
        if paragraph_id is None or paragraph.id == paragraph_id
    ]
    if paragraph_id is not None and not in_scope:
        raise CandidateConstructionError(
            "The command targeted a paragraph that is not in this section.",
            section_id=section.id,
            paragraph_id=paragraph_id,
        )

    targets: list[ParagraphTarget] = []
    skipped: list[tuple[str, str]] = []

    for paragraph in in_scope:
        reason = _ineligible(paragraph, effective)
        if reason:
            skipped.append((paragraph.id, reason))
            continue
        words = paragraph.word_count()
        targets.append(
            ParagraphTarget(
                paragraph_id=paragraph.id,
                words_before=words,
                target_words=max(MIN_PARAGRAPH_WORDS, round(words * (1.0 - effective))),
            )
        )

    if not targets:
        scope = "This paragraph" if paragraph_id else "No paragraph in this section"
        verb = "cannot" if paragraph_id else "can"
        raise CandidateConstructionError(
            f"{scope} {verb} be shortened without losing content: "
            f"{skipped[0][1] if skipped else 'nothing was eligible'}.",
            section_id=section.id,
            paragraph_id=paragraph_id,
            paragraphs=len(in_scope),
        )

    return ShortenPlan(
        section_id=section.id,
        ratio=effective,
        targets=tuple(targets),
        skipped=tuple(skipped),
    )


def _ineligible(paragraph: Paragraph, ratio: float) -> str:
    words = paragraph.word_count()
    if words < MIN_PARAGRAPH_WORDS:
        return f"only {words} words"
    if round(words * ratio) < MIN_ABSOLUTE_REDUCTION:
        return "the requested reduction is too small to be meaningful"
    if paragraph.citation_ids and words < len(paragraph.citation_ids) * 12:
        return "mostly citations, so shortening could only drop them"
    return ""


def _clamp(ratio: float) -> float:
    return min(0.5, max(0.05, ratio))
