from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from sqlalchemy.orm import Session

from app.db.repositories import new_id
from app.domain.document import Document, Paragraph
from app.domain.errors import GroundingValidationError
from app.domain.hashing import text_sha256
from app.domain.review import ClaimAnchor, FindingKind, ReviewFinding
from app.domain.source import ProviderWork
from app.observability import get_logger
from app.providers.llm import Provenance, StructuredLLM
from app.providers.openalex import normalise_doi
from app.providers.session import ProviderSession
from app.services.citations.bibliography import BibliographyIndex
from app.services.citations.sources import SourceStore
from app.services.deadline import Deadline
from app.services.parser.segmenter import SEGMENTER_VERSION
from app.services.research import rerank as rerank_prompt
from app.services.research.allowlist import Allowlist
from app.services.research.retrieval import AcademicRetrievalService, Candidate
from app.services.review.claims import ClaimTarget
from app.settings import get_settings

_SECTION_PRIORITY: tuple[tuple[re.Pattern[str], int], ...] = (
    (re.compile(r"introduct|background|motivat|related|prior work|literature"), 0),
    (re.compile(r"discuss|conclu|limitat|future work"), 1),
    (re.compile(r"method|approach|experiment|result|evaluat|implement|appendix"), 3),
)
_DEFAULT_PRIORITY = 2


@dataclass(frozen=True)
class SearchNote:
    sentence_id: str
    detail: str


logger = get_logger(__name__)


@dataclass
class MissingWorkOutcome:
    findings: list[ReviewFinding] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)
    paragraphs_searched: int = 0
    claims_searched: int = 0
    suggestions: int = 0
    grounding_rejections: int = 0


class MissingWorkReviewer:
    def __init__(
        self,
        session: Session,
        paper_id: str,
        *,
        llm: StructuredLLM,
        provider_session: ProviderSession,
        sources: SourceStore,
        retrieval: AcademicRetrievalService | None = None,
    ) -> None:
        self.session = session
        self.paper_id = paper_id
        self.llm = llm
        self.provider_session = provider_session
        self.sources = sources
        self.retrieval = retrieval or AcademicRetrievalService(provider_session)

    def run(
        self,
        document: Document,
        extract: Callable[[Paragraph], tuple[ClaimTarget, ...]],
        deadline: Deadline,
    ) -> MissingWorkOutcome:
        settings = get_settings()
        bibliography = BibliographyIndex.with_snapshots(self.session, self.paper_id, document)
        outcome = MissingWorkOutcome()
        budget = settings.max_missing_work_claims

        paragraphs = paragraphs_worth_searching(document, settings.max_review_paragraphs)
        for paragraph in paragraphs:
            if budget <= 0 or deadline.expired:
                break
            outcome.paragraphs_searched += 1
            try:
                targets = list(extract(paragraph))
            except GroundingValidationError as rejected:
                outcome.grounding_rejections += 1
                logger.warning(
                    "review.missing_work_claims_rejected",
                    extra={"paragraph_id": paragraph.id, **rejected.details},
                )
                continue
            for target in targets:
                if budget <= 0 or deadline.expired:
                    break
                deadline.check("review.missing_work")
                budget -= 1
                outcome.claims_searched += 1
                try:
                    self._for_claim(paragraph, target, bibliography, outcome, deadline)
                except GroundingValidationError as rejected:
                    outcome.grounding_rejections += 1
                    outcome.notes[target.sentence.id] = (
                        "Work was found for this claim, but the ranking response was "
                        "discarded because it referred to a candidate that was never "
                        "offered. Nothing is suggested here rather than something "
                        "unverifiable."
                    )
                    logger.warning(
                        "review.missing_work_grounding_rejected",
                        extra={"paragraph_id": paragraph.id, **rejected.details},
                    )

        return outcome

    def _for_claim(
        self,
        paragraph: Paragraph,
        target: ClaimTarget,
        bibliography: BibliographyIndex,
        outcome: MissingWorkOutcome,
        deadline: Deadline,
    ) -> None:
        topic = target.topic.strip()
        if not topic:
            outcome.notes[target.sentence.id] = (
                "No search topic could be derived for this claim, so no search was run."
            )
            return

        found = self.retrieval.search(topic, deadline)
        citable = [candidate for candidate in found.candidates if _link_for(candidate.work)]
        novel = [candidate for candidate in citable if not bibliography.contains(candidate.work)]

        if not novel:
            outcome.notes[target.sentence.id] = self._nothing_note(
                searched=len(found.candidates), already_cited=len(citable) - len(novel)
            )
            return

        settings = get_settings()
        ordered, provenance = self._rank(target, novel, deadline)
        ranked = ordered[: settings.max_suggestions_per_claim]
        if not ranked:
            outcome.notes[target.sentence.id] = self._nothing_note(
                searched=len(found.candidates), already_cited=len(citable) - len(novel)
            )
            return

        suggestions = tuple(self.sources.snapshot(candidate.work) for candidate, _ in ranked)
        outcome.suggestions += len(suggestions)
        outcome.findings.append(
            ReviewFinding(
                id=new_id("find"),
                kind=FindingKind.MISSING_WORK,
                claim_anchor=_anchor_for(paragraph, target),
                reason=(
                    f"{len(suggestions)} work(s) found by searching {self._coverage()} for "
                    f"{topic!r} are relevant to this claim and are not in your bibliography."
                ),
                suggested_source_record_ids=suggestions,
                suggestion_rationales=tuple(rationale for _, rationale in ranked),
                provider=provenance.provider,
                model=provenance.model,
                prompt_version=provenance.prompt_version,
            )
        )

    def _rank(
        self, target: ClaimTarget, candidates: list[Candidate], deadline: Deadline
    ) -> tuple[list[tuple[Candidate, str]], Provenance]:
        renumbered = [
            replace(candidate, id=f"cand_{index:03d}") for index, candidate in enumerate(candidates)
        ]
        by_id = {candidate.id: candidate for candidate in renumbered}
        allowlist = Allowlist("candidate", by_id)

        prompt = rerank_prompt.build_prompt(
            target.sentence.plain_text,
            [(candidate.id, candidate.summary()) for candidate in renumbered],
            get_settings().max_suggestions_per_claim,
        )
        result = self.llm.complete_structured(
            prompt, rerank_prompt.RerankResponse, remaining_seconds=deadline.remaining()
        )
        response = result.value

        seen: set[str] = set()
        ordered: list[tuple[Candidate, str]] = []
        for entry in sorted(response.ranked, key=lambda item: item.rank):
            candidate = allowlist.resolve(entry.candidate_id, prompt=prompt.name)
            if candidate.id in seen:
                continue
            seen.add(candidate.id)
            ordered.append((candidate, entry.rationale))
        return ordered, Provenance.of(result)

    def _coverage(self) -> str:
        degraded = {item.provider for item in self.provider_session.degradations}
        if not degraded:
            return "both providers"
        remaining = [name for name in ("OPENALEX", "SEMANTIC_SCHOLAR") if name not in degraded]
        if not remaining:
            return "no reachable provider"
        return f"{_readable(remaining[0])} only ({_readable(sorted(degraded)[0])} was unavailable)"

    def _nothing_note(self, *, searched: int, already_cited: int) -> str:
        degraded = self.provider_session.degradations
        if degraded:
            names = ", ".join(sorted({item.provider for item in degraded}))
            return (
                f"This claim was searched, but {names} was unavailable during this run, "
                "so the search did not cover everything it should have."
            )
        if already_cited:
            return (
                f"{self._coverage().capitalize()} searched for this claim. Every relevant work "
                f"found ({already_cited}) is already in your bibliography."
            )
        if searched:
            return (
                f"{self._coverage().capitalize()} searched for this claim. Nothing returned had "
                "metadata complete enough to cite."
            )
        return f"{self._coverage().capitalize()} searched for this claim and returned nothing."


def paragraphs_worth_searching(document: Document, limit: int) -> list[Paragraph]:
    ranked = sorted(
        enumerate(document.paragraphs()),
        key=lambda pair: (_priority(document, pair[1]), pair[0]),
    )
    return [paragraph for _, paragraph in ranked[:limit]]


def _priority(document: Document, paragraph: Paragraph) -> int:
    section = document.section_of(paragraph.id)
    title = (section.title if section else "").casefold()
    for pattern, priority in _SECTION_PRIORITY:
        if pattern.search(title):
            return priority
    return _DEFAULT_PRIORITY


def _link_for(work: ProviderWork) -> str | None:
    if work.url and work.url.startswith("https://"):
        return work.url
    doi = normalise_doi(work.doi)
    return f"https://doi.org/{doi}" if doi else None


def _anchor_for(paragraph: Paragraph, target: ClaimTarget) -> ClaimAnchor:
    return ClaimAnchor(
        paragraph_id=paragraph.id,
        sentence_index=target.sentence.index,
        sentence_id=target.sentence.id,
        text_hash=text_sha256(target.sentence.plain_text),
        segmenter_version=SEGMENTER_VERSION,
    )


_PROVIDER_NAMES = {"OPENALEX": "OpenAlex", "SEMANTIC_SCHOLAR": "Semantic Scholar"}


def _readable(provider: str) -> str:
    return _PROVIDER_NAMES.get(provider, provider.replace("_", " ").title())
