from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.db.models import ReferenceResolution, ReviewFindingRow, ReviewRun
from app.db.repositories import new_id
from app.domain.document import Document, Paragraph
from app.domain.errors import (
    GroundingValidationError,
    LLMNotConfiguredError,
    NotFoundError,
    ProviderError,
)
from app.domain.hashing import text_sha256
from app.domain.lifecycle import RunStatus
from app.domain.reference import ReferenceRecord
from app.domain.review import (
    MODEL_SELECTABLE_VERDICTS,
    ClaimAnchor,
    EvidenceAnchor,
    FindingKind,
    ProviderDegradation,
    ReviewFinding,
    SupportVerdict,
)
from app.domain.source import ProviderWork
from app.observability import get_logger
from app.providers.llm import Provenance, StructuredLLM
from app.providers.session import ProviderSession
from app.services.citations.context_extractor import (
    EvidenceSpan,
    span_listing,
    split_abstract,
)
from app.services.citations.resolver import ReferenceResolver, Resolution
from app.services.citations.sources import SourceStore, record_attempts
from app.services.deadline import Deadline
from app.services.parser.segmenter import SEGMENTER_VERSION, Sentence, segment_paragraph
from app.services.research.allowlist import Allowlist, require_choice
from app.services.review import claims as claims_prompt
from app.services.review import support as support_prompt
from app.services.review.claims import ClaimTarget
from app.services.review.missing_work import MissingWorkReviewer
from app.settings import get_settings

logger = get_logger(__name__)


@dataclass
class ReviewOutcome:
    findings: list[ReviewFinding] = field(default_factory=list)
    degradations: tuple[ProviderDegradation, ...] = ()
    stats: dict[str, int] = field(default_factory=dict)
    resolutions: dict[str, Resolution] = field(default_factory=dict)
    source_record_ids: dict[str, str] = field(default_factory=dict)


class ReviewRunner:
    def __init__(
        self,
        session: Session,
        paper_id: str,
        *,
        llm: StructuredLLM,
        provider_session: ProviderSession,
        resolver: ReferenceResolver | None = None,
    ) -> None:
        self.session = session
        self.paper_id = paper_id
        self.llm = llm
        self.provider_session = provider_session
        self.resolver = resolver or ReferenceResolver(provider_session)
        self.sources = SourceStore(session, paper_id)
        self.missing_work = MissingWorkReviewer(
            session,
            paper_id,
            llm=llm,
            provider_session=provider_session,
            sources=self.sources,
        )
        self._resolutions: dict[str, Resolution] = {}
        self._records: dict[str, str] = {}
        self._claims: dict[str, tuple[ClaimTarget, ...]] = {}
        self._support_assertions_total = 0
        self._support_assertions_completed = 0
        self._grounding_rejections = 0

    def run(self, document: Document, deadline: Deadline) -> ReviewOutcome:
        if not self.llm.configured:
            raise LLMNotConfiguredError(
                "Peer review needs a configured model, so this operation is unavailable."
            )

        settings = get_settings()
        outcome = ReviewOutcome()
        reviewed = 0

        support_paragraphs = _paragraphs_worth_reviewing(document, settings.max_review_paragraphs)
        for paragraph in support_paragraphs:
            deadline.check("review.paragraph")
            try:
                outcome.findings.extend(self._review_paragraph(document, paragraph, deadline))
            except GroundingValidationError as rejected:
                self._grounding_rejections = getattr(self, "_grounding_rejections", 0) + 1
                logger.warning(
                    "review.grounding_rejected",
                    extra={"paragraph_id": paragraph.id, **rejected.details},
                )
            reviewed += 1

        missing = self.missing_work.run(
            document, lambda paragraph: self._claims_for(paragraph, deadline), deadline
        )
        outcome.findings = _with_search_notes(outcome.findings, missing.notes)
        outcome.findings.extend(missing.findings)
        outcome.findings.extend(self._structural_findings(document))

        outcome.degradations = self.provider_session.degradations
        outcome.resolutions = dict(self._resolutions)
        outcome.source_record_ids = dict(self._records)
        outcome.stats = {
            "paragraphs_reviewed": reviewed,
            "paragraphs_total": len(document.paragraphs()),
            "paragraph_limit": settings.max_review_paragraphs,
            "references_total": len(document.references),
            "findings": len(outcome.findings),
            "support_assertions_total": getattr(self, "_support_assertions_total", 0),
            "support_assertions_completed": getattr(self, "_support_assertions_completed", 0),
            "grounding_rejections": getattr(self, "_grounding_rejections", 0)
            + missing.grounding_rejections,
            "claims_searched": missing.claims_searched,
            "missing_work_paragraphs_searched": missing.paragraphs_searched,
            "works_suggested": missing.suggestions,
            "provider_calls": self.provider_session.total_calls,
            "references_resolved": sum(
                1 for resolution in self._resolutions.values() if resolution.resolved
            ),
            "references_examined": len(self._resolutions),
        }
        return outcome

    def _review_paragraph(
        self, document: Document, paragraph: Paragraph, deadline: Deadline
    ) -> list[ReviewFinding]:
        sentences = segment_paragraph(paragraph)
        if not sentences:
            return []

        claimed = {target.sentence.id for target in self._claims_for(paragraph, deadline)}
        findings: list[ReviewFinding] = []
        for sentence in sentences:
            if sentence.id not in claimed:
                continue
            anchor = _anchor_for(paragraph, sentence)
            if sentence.citation_ids:
                findings.extend(self._review_citations(document, sentence, anchor, deadline))
            else:
                findings.append(
                    ReviewFinding(
                        id=new_id("find"),
                        kind=FindingKind.UNCITED_CLAIM,
                        claim_anchor=anchor,
                        reason=(
                            "This sentence asserts something a source could support and "
                            "carries no citation."
                        ),
                    )
                )
        return findings

    def _claims_for(self, paragraph: Paragraph, deadline: Deadline) -> tuple[ClaimTarget, ...]:
        if paragraph.id in self._claims:
            return self._claims[paragraph.id]

        sentences = segment_paragraph(paragraph)
        if not sentences:
            self._claims[paragraph.id] = ()
            return ()

        allowlist = Allowlist("sentence", {sentence.id: sentence for sentence in sentences})
        prompt = claims_prompt.build_prompt(
            [(sentence.id, sentence.plain_text) for sentence in sentences],
            get_settings().max_claims_per_paragraph,
        )
        result = self.llm.complete_structured(
            prompt,
            claims_prompt.ClaimExtractionResponse,
            remaining_seconds=deadline.remaining(),
        )
        targets = tuple(
            ClaimTarget(
                sentence=allowlist.resolve(claim.sentence_id, prompt=prompt.name),
                topic=claim.topic,
            )
            for claim in result.value.claims
            if claim.is_citable_claim
        )
        self._claims[paragraph.id] = targets
        return targets

    def _review_citations(
        self,
        document: Document,
        sentence: Sentence,
        anchor: ClaimAnchor,
        deadline: Deadline,
    ) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        for citation_id in sentence.citation_ids:
            node = document.citations.get(citation_id)
            if node is None:
                continue
            if node.is_unlinked:
                findings.append(
                    ReviewFinding(
                        id=new_id("find"),
                        kind=FindingKind.UNLINKED_CITATION,
                        claim_anchor=anchor,
                        occurrence_id=citation_id,
                        reason=(
                            f"The marker {node.raw_marker!r} could not be matched to a "
                            "bibliography entry, so its support cannot be checked."
                        ),
                    )
                )
                continue
            for reference_id in node.reference_ids:
                reference = document.reference(reference_id)
                if reference is None:
                    continue
                self._support_assertions_total = getattr(self, "_support_assertions_total", 0) + 1
                finding = self._verdict_for(sentence, anchor, citation_id, reference, deadline)
                self._support_assertions_completed = (
                    getattr(self, "_support_assertions_completed", 0) + 1
                )
                findings.append(finding)
        return findings

    def _verdict_for(
        self,
        sentence: Sentence,
        anchor: ClaimAnchor,
        occurrence_id: str,
        reference: ReferenceRecord,
        deadline: Deadline,
    ) -> ReviewFinding:
        resolution = self._resolve(reference, deadline)

        if resolution.work is None:
            return self._finding(anchor, occurrence_id, reference, SupportVerdict.SOURCE_UNRESOLVED)
        if not resolution.resolved:
            return self._finding(
                anchor, occurrence_id, reference, SupportVerdict.SOURCE_IDENTITY_UNCERTAIN
            )

        work = self._with_abstract(resolution.work, deadline)
        record_id = self._snapshot(work)
        if not work.has_abstract or work.abstract is None:
            return self._finding(
                anchor, occurrence_id, reference, SupportVerdict.EVIDENCE_UNAVAILABLE
            )

        spans = split_abstract(work.abstract)
        if not spans:
            return self._finding(
                anchor, occurrence_id, reference, SupportVerdict.EVIDENCE_UNAVAILABLE
            )

        verdict, evidence, reason, provenance = self._ask_support(
            sentence.plain_text, spans, record_id, deadline
        )
        return self._finding(
            anchor,
            occurrence_id,
            reference,
            verdict,
            evidence=evidence,
            reason=reason,
            provenance=provenance,
        )

    def _ask_support(
        self,
        claim_text: str,
        spans: tuple[EvidenceSpan, ...],
        record_id: str,
        deadline: Deadline,
    ) -> tuple[SupportVerdict, tuple[EvidenceAnchor, ...], str, Provenance]:
        allowlist = Allowlist("span", {span.id: span for span in spans})
        prompt = support_prompt.build_prompt(claim_text, span_listing(spans))
        result = self.llm.complete_structured(
            prompt, support_prompt.SupportResponse, remaining_seconds=deadline.remaining()
        )

        chosen = require_choice(
            result.value.verdict,
            [verdict.value for verdict in MODEL_SELECTABLE_VERDICTS],
            prompt=prompt.name,
            field="verdict",
        )
        selected = allowlist.resolve_all(result.value.evidence_span_ids, prompt=prompt.name)
        anchors = tuple(span.anchor(record_id) for span in selected)
        return SupportVerdict(chosen), anchors, result.value.reason, Provenance.of(result)

    def _finding(
        self,
        anchor: ClaimAnchor,
        occurrence_id: str,
        reference: ReferenceRecord,
        verdict: SupportVerdict,
        *,
        evidence: tuple[EvidenceAnchor, ...] = (),
        reason: str = "",
        provenance: Provenance | None = None,
    ) -> ReviewFinding:
        return ReviewFinding(
            id=new_id("find"),
            kind=FindingKind.CITATION_SUPPORT,
            claim_anchor=anchor,
            occurrence_id=occurrence_id,
            reference_id=reference.id,
            verdict=verdict,
            evidence=evidence,
            reason=reason or _server_reason(verdict),
            provider=provenance.provider if provenance else None,
            model=provenance.model if provenance else None,
            prompt_version=provenance.prompt_version if provenance else None,
        )

    def _structural_findings(self, document: Document) -> list[ReviewFinding]:
        findings: list[ReviewFinding] = []
        for reference_id, resolution in sorted(self._resolutions.items()):
            if resolution.resolved:
                continue
            reference = document.reference(reference_id)
            if reference is None:
                continue
            findings.append(
                ReviewFinding(
                    id=new_id("find"),
                    kind=FindingKind.UNRESOLVED_REFERENCE,
                    claim_anchor=_null_anchor(),
                    reference_id=reference_id,
                    reason=(
                        "This bibliography entry could not be matched to a provider "
                        f"record ({resolution.method.value}), so nothing citing it "
                        "could be checked."
                    ),
                )
            )
        return findings

    def _resolve(self, reference: ReferenceRecord, deadline: Deadline) -> Resolution:
        if reference.id in self._resolutions:
            return self._resolutions[reference.id]
        try:
            resolution = self.resolver.resolve(reference, deadline)
        except ProviderError:
            resolution = Resolution.unresolved()
        self._resolutions[reference.id] = resolution
        return resolution

    def _with_abstract(self, work: ProviderWork, deadline: Deadline) -> ProviderWork:
        try:
            return self.resolver.fetch_abstract(work, deadline)
        except ProviderError:
            return work

    def _snapshot(self, work: ProviderWork) -> str:
        key = f"{work.provider.value}:{work.external_id}"
        if key not in self._records:
            self._records[key] = self.sources.snapshot(work)
        return self._records[key]


def _with_search_notes(findings: list[ReviewFinding], notes: dict[str, str]) -> list[ReviewFinding]:
    enriched: list[ReviewFinding] = []
    for finding in findings:
        if finding.kind is not FindingKind.UNCITED_CLAIM:
            enriched.append(finding)
            continue
        note = notes.get(finding.claim_anchor.sentence_id)
        if note is None:
            note = (
                "This claim was not searched in this run because the bounded search "
                "budget was spent on higher-priority paragraphs."
            )
        enriched.append(finding.model_copy(update={"reason": f"{finding.reason} {note}"}))
    return enriched


def _paragraphs_worth_reviewing(document: Document, limit: int) -> list[Paragraph]:
    ordered = sorted(
        enumerate(document.paragraphs()),
        key=lambda pair: (-len(pair[1].citation_ids), pair[0]),
    )
    return [paragraph for _, paragraph in ordered[:limit]]


def _anchor_for(paragraph: Paragraph, sentence: Sentence) -> ClaimAnchor:
    return ClaimAnchor(
        paragraph_id=paragraph.id,
        sentence_index=sentence.index,
        sentence_id=sentence.id,
        text_hash=text_sha256(sentence.plain_text),
        segmenter_version=SEGMENTER_VERSION,
    )


def _null_anchor() -> ClaimAnchor:
    return ClaimAnchor(
        paragraph_id="",
        sentence_index=-1,
        sentence_id="",
        text_hash="",
        segmenter_version=SEGMENTER_VERSION,
    )


def _server_reason(verdict: SupportVerdict) -> str:
    return {
        SupportVerdict.SOURCE_UNRESOLVED: (
            "This reference could not be matched to a provider record, so its support "
            "was not assessed."
        ),
        SupportVerdict.SOURCE_IDENTITY_UNCERTAIN: (
            "Only a weak metadata match was found, so a verdict here would be about a "
            "work we cannot confirm is the one cited."
        ),
        SupportVerdict.EVIDENCE_UNAVAILABLE: (
            "No abstract was available for this work, so there was nothing to assess it against."
        ),
    }.get(verdict, "")


def claim_run(session: Session, *, paper_id: str, revision_id: str) -> ReviewRun:
    run = ReviewRun(
        id=new_id("rev"),
        paper_id=paper_id,
        revision_id=revision_id,
        status=RunStatus.PENDING.value,
    )
    session.add(run)
    session.flush()
    return run


def persist(
    session: Session,
    *,
    run_id: str,
    paper_id: str,
    outcome: ReviewOutcome,
    provider_session: ProviderSession,
) -> ReviewRun:
    run = session.get(ReviewRun, run_id)
    if run is None:
        raise NotFoundError("This review run no longer exists.", run_id=run_id)

    run.status = RunStatus.COMPLETED.value
    run.degradations = [item.model_dump(mode="json") for item in outcome.degradations]
    run.stats = outcome.stats
    session.flush()

    for finding in outcome.findings:
        session.add(
            ReviewFindingRow(
                id=finding.id,
                run_id=run.id,
                paper_id=paper_id,
                kind=finding.kind.value,
                occurrence_id=finding.occurrence_id,
                reference_id=finding.reference_id,
                verdict=finding.verdict.value if finding.verdict else None,
                claim_anchor=finding.claim_anchor.model_dump(mode="json"),
                evidence=[anchor.model_dump(mode="json") for anchor in finding.evidence],
                reason=finding.reason,
                suggested_source_record_ids=list(finding.suggested_source_record_ids),
                suggestion_rationales=list(finding.suggestion_rationales),
                provider=finding.provider,
                model=finding.model,
                prompt_version=finding.prompt_version,
            )
        )
    _persist_resolutions(session, paper_id=paper_id, run_id=run.id, outcome=outcome)
    record_attempts(session, paper_id, provider_session)
    session.flush()
    return run


def _persist_resolutions(
    session: Session,
    *,
    paper_id: str,
    run_id: str,
    outcome: ReviewOutcome,
) -> None:
    existing = {
        row.reference_id: row
        for row in session.query(ReferenceResolution).filter_by(paper_id=paper_id)
    }
    for reference_id, resolution in outcome.resolutions.items():
        record_id = None
        if resolution.resolved and resolution.work is not None:
            work = resolution.work
            record_id = outcome.source_record_ids.get(f"{work.provider.value}:{work.external_id}")

        row = existing.get(reference_id)
        if row is None:
            row = ReferenceResolution(
                id=new_id("refres"), paper_id=paper_id, reference_id=reference_id
            )
            session.add(row)
        row.run_id = run_id
        row.method = resolution.method.value
        row.confidence = resolution.confidence.value
        row.source_record_id = record_id


def record_failure(
    session: Session,
    *,
    run_id: str,
    paper_id: str,
    revision_id: str,
    failure_code: str,
    provider_session: ProviderSession,
) -> ReviewRun:
    run = session.get(ReviewRun, run_id)
    if run is None:
        run = ReviewRun(id=run_id, paper_id=paper_id, revision_id=revision_id)
        session.add(run)

    run.status = RunStatus.FAILED.value
    run.failure_code = failure_code
    run.degradations = [item.model_dump(mode="json") for item in provider_session.degradations]
    run.stats = {}
    record_attempts(session, paper_id, provider_session)
    session.flush()
    return run
