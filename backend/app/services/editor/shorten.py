"""SHORTEN_SECTION.

The safety spine, in order, per paragraph:

1. Tokenise, so citations reach the model as opaque ids rather than as rendered
   labels a rewrite could renumber.
2. Rewrite one paragraph. Never a section: a model that can restructure a section
   can silently drop a paragraph, and paragraph identity is what every anchor and
   finding depends on.
3. Audit the tokens. Duplicated, invented, or mangled is a typed error.
4. Check semantic novelty, because tokens cannot see an invented statistic. "may
   reduce latency" becoming "reduces latency by 30%" leaves every token intact
   and the manuscript false.
5. Only then stage the paragraph on the builder.

A paragraph that fails novelty keeps its original wording and is reported; the
rest of the section still goes through. The stricter rule -- discard the whole
section for one bad paragraph -- made shortening an introduction fail almost
every time, and the researcher reads a per-paragraph diff anyway, so nothing
lands unreviewed either way. Only when *every* paragraph is refused does the
command fail outright, because then there is no edit left to show.

``shorten_extractive_only`` replaces the rewrite with sentence deletion, dropping
sentences from the end until the paragraph hits its word target. It is a fallback,
not a mode worth defaulting to: no model is called at all, so the result is
truncation rather than shortening -- it will cut a sentence that introduces the
list below it as readily as a redundant aside. It exists for a model that cannot
be trusted to rewrite, and it is off by default.

Step 4 is the safeguard that makes rewriting acceptable, and it is the only one
here that is itself a model judgement, so its limits are worth stating. On at
least one adversarial pair it missed a dropped qualifier:

    "In mice, the treatment improved survival over a twelve week period."
    -> "The treatment improved survival."

Tokens cannot see that; the researcher reading the diff can, and the diff is the
last gate before anything is applied. That is the trade this feature makes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.delta import ComputedEditDelta
from app.domain.document import Document, Paragraph
from app.domain.errors import CandidateConstructionError, UnsupportedNoveltyError
from app.domain.verification import CheckName, CheckStatus, VerificationCheck
from app.providers.llm import StructuredLLM
from app.services.deadline import Deadline
from app.services.editor import novelty as novelty_prompt
from app.services.editor import rewriter as rewrite_prompt
from app.services.editor import tokens
from app.services.editor.candidate_revision import CandidateRevisionBuilder
from app.services.editor.delta_engine import compute_delta
from app.services.editor.planner import ShortenPlan, plan_shortening
from app.services.parser.segmenter import detokenize, segment_text, tokenize
from app.settings import get_settings

TARGET_TOLERANCE = 0.6


@dataclass
class ShortenResult:
    document: Document
    delta: ComputedEditDelta
    plan: ShortenPlan
    checks: list[VerificationCheck] = field(default_factory=list)

    dropped_citations: dict[str, tuple[str, ...]] = field(default_factory=dict)
    extractive_only: bool = False


def shorten_section(
    document: Document,
    section_id: str,
    *,
    llm: StructuredLLM,
    deadline: Deadline,
    ratio: float | None = None,
    paragraph_id: str | None = None,
) -> ShortenResult:
    """Shorten one section, or one paragraph of it, or raise without staging."""
    section = document.section(section_id)
    if section is None:
        raise CandidateConstructionError(
            "The command targeted a section that is not in this document.",
            section_id=section_id,
        )

    settings = get_settings()
    extractive = settings.shorten_extractive_only
    plan = plan_shortening(section, ratio, paragraph_id)
    builder = CandidateRevisionBuilder(document)
    checks: list[VerificationCheck] = []
    dropped: dict[str, tuple[str, ...]] = {}
    refused: dict[str, list[str]] = {}
    staged: list[str] = []

    for target in plan.targets:
        deadline.check("shorten.paragraph")
        paragraph = document.paragraph(target.paragraph_id)
        if paragraph is None:
            continue

        before = tokenize(paragraph)
        after = (
            _extractive_shorten(before, target.target_words)
            if extractive
            else _rewrite(before, target.target_words, llm, deadline)
        )

        if not extractive:
            try:
                checks.append(_check_novelty(before, after, llm, deadline, target.paragraph_id))
            except UnsupportedNoveltyError as rejected:
                refused[target.paragraph_id] = sorted(rejected.details.get("kinds") or ())
                continue

        audit = tokens.audit(before, after, target.paragraph_id)
        if audit.dropped:
            dropped[target.paragraph_id] = audit.dropped

        builder.replace_paragraph(target.paragraph_id, after)
        staged.append(target.paragraph_id)

    if plan.targets and len(refused) == len(plan.targets):
        raise UnsupportedNoveltyError(
            "Every paragraph's rewrite asserted something the original does not.",
            paragraph_id=next(iter(refused)),
            kinds=sorted({kind for kinds in refused.values() for kind in kinds}),
        )

    if refused:
        checks.append(
            VerificationCheck(
                name=CheckName.SEMANTIC_NOVELTY,
                status=CheckStatus.WARNED,
                detail=(
                    f"{len(refused)} of {len(plan.targets)} paragraphs were left unchanged "
                    "because the shortened text asserted something the original did not."
                ),
            )
        )

    checks.append(
        VerificationCheck(
            name=CheckName.PROTECTED_TOKENS,
            status=CheckStatus.PASSED,
            detail=(
                "No citation token was invented, duplicated, or altered in "
                f"{len(staged)} rewritten paragraphs."
            ),
        )
    )

    candidate = builder.build()
    return ShortenResult(
        document=candidate,
        delta=compute_delta(document, candidate),
        plan=plan,
        checks=checks,
        dropped_citations=dropped,
        extractive_only=extractive,
    )


def _rewrite(tokenised: str, target_words: int, llm: StructuredLLM, deadline: Deadline) -> str:
    prompt = rewrite_prompt.build_prompt(tokenised, target_words)
    result = llm.complete_structured(
        prompt, rewrite_prompt.RewriteResponse, remaining_seconds=deadline.remaining()
    )

    return result.value.revised_text.strip()


def _check_novelty(
    before: str,
    after: str,
    llm: StructuredLLM,
    deadline: Deadline,
    paragraph_id: str,
) -> VerificationCheck:
    """The check protected tokens cannot perform.

    Raises rather than warning. A researcher cannot be asked to acknowledge a
    fabricated statistic, because acknowledging it would require already knowing
    it was fabricated -- which is the thing this check exists to establish.
    """
    prompt = novelty_prompt.build_prompt(detokenize(before), detokenize(after))
    result = llm.complete_structured(
        prompt, novelty_prompt.NoveltyResponse, remaining_seconds=deadline.remaining()
    )

    if result.value.has_unsupported_novelty or result.value.findings:
        raise UnsupportedNoveltyError(
            "The shortened text asserts something the original does not.",
            paragraph_id=paragraph_id,
            kinds=sorted({finding.kind for finding in result.value.findings}),
        )

    return VerificationCheck(
        name=CheckName.SEMANTIC_NOVELTY,
        status=CheckStatus.PASSED,
        detail=f"No unentailed assertion found in {paragraph_id}.",
        prompt_version=result.prompt_version,
        model=result.model,
        provider=result.provider,
    )


def _extractive_shorten(tokenised: str, target_words: int) -> str:
    """Shorten by deleting whole sentences, inventing nothing.

    Sentences are dropped from the end, which preserves the paragraph's opening
    claim and its topic sentence. Sentences carrying citations are kept: dropping
    them would remove the author's support, and this mode exists precisely
    because the safety net that would have caught a bad rewrite is unavailable.
    """
    spans = segment_text(tokenised)
    if len(spans) <= 1:
        return tokenised

    kept: list[tuple[int, int]] = []
    words = 0
    for start, end in spans:
        sentence = tokenised[start:end]
        sentence_words = len(detokenize(sentence).split())
        carries_citation = "[[CITE:" in sentence
        if kept and words + sentence_words > target_words and not carries_citation:
            continue
        kept.append((start, end))
        words += sentence_words

    return " ".join(tokenised[start:end].strip() for start, end in kept)


def target_shortfall(plan: ShortenPlan, delta: ComputedEditDelta) -> float:
    """How much of the requested reduction was actually achieved, as a fraction."""
    requested = plan.words_before - plan.words_target
    if requested <= 0:
        return 1.0
    achieved = delta.scope_words_before - delta.scope_words_after
    return max(0.0, achieved / requested)


def paragraph_words(paragraph: Paragraph) -> int:
    return paragraph.word_count()
