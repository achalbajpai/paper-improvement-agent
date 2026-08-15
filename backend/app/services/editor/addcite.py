from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Protocol

from app.domain.delta import ComputedEditDelta
from app.domain.document import Document, Paragraph
from app.domain.errors import CandidateConstructionError, NoResultsError
from app.domain.reference import ReferenceRecord, ResolutionConfidence, ResolutionMethod
from app.domain.review import SupportVerdict
from app.domain.source import ProviderWork
from app.domain.verification import CheckName, CheckStatus, VerificationCheck
from app.providers.llm import StructuredLLM
from app.services.citations.bibliography import BibliographyIndex
from app.services.citations.context_extractor import span_listing, split_abstract
from app.services.deadline import Deadline
from app.services.editor import insertion
from app.services.editor import selection as selection_prompt
from app.services.editor.candidate_revision import CandidateRevisionBuilder
from app.services.editor.delta_engine import compute_delta
from app.services.parser.segmenter import Sentence, segment_paragraph, tokenize
from app.services.research import rerank as rerank_prompt
from app.services.research.allowlist import Allowlist, require_choice
from app.services.research.retrieval import AcademicRetrievalService, Candidate
from app.services.review import claims as claims_prompt
from app.services.review import support as support_prompt
from app.settings import get_settings

INSERTABLE_VERDICTS = frozenset({SupportVerdict.SUPPORTED, SupportVerdict.PARTIALLY_SUPPORTED})


class SnapshotStore(Protocol):
    def snapshot(self, work: ProviderWork) -> str: ...


@dataclass(frozen=True)
class AddedCitation:
    paragraph_id: str
    sentence_id: str
    citation_id: str
    reference: ReferenceRecord
    source_record_id: str
    verdict: SupportVerdict
    rationale: str
    merged_into: str | None = None


@dataclass
class AddCitationsResult:
    document: Document
    delta: ComputedEditDelta
    added: list[AddedCitation] = field(default_factory=list)
    checks: list[VerificationCheck] = field(default_factory=list)

    rejected: list[tuple[str, str]] = field(default_factory=list)


class CitationAdder:
    def __init__(
        self,
        *,
        llm: StructuredLLM,
        retrieval: AcademicRetrievalService,
        sources: SnapshotStore,
    ) -> None:
        self.llm = llm
        self.retrieval = retrieval
        self.sources = sources
        self._next_ordinal = 0

    def run(self, document: Document, section_id: str, deadline: Deadline) -> AddCitationsResult:
        section = document.section(section_id)
        if section is None:
            raise CandidateConstructionError(
                "The command targeted a section that is not in this document.",
                section_id=section_id,
            )

        self._next_ordinal = _next_added_ordinal(document)
        builder = CandidateRevisionBuilder(document)
        added: list[AddedCitation] = []
        rejected: list[tuple[str, str]] = []
        checks: list[VerificationCheck] = []
        searched = False
        settings = get_settings()

        for paragraph in section.paragraphs[: settings.max_add_citation_paragraphs]:
            if len(added) >= settings.max_added_citations_per_operation:
                break
            deadline.check("addcite.paragraph")
            sentences = segment_paragraph(paragraph)
            targets = self._citable_sentences(sentences, deadline)
            if not targets:
                continue

            searched = True
            candidates = self._retrieve(document, targets, deadline)
            if not candidates:
                continue

            remaining = settings.max_added_citations_per_operation - len(added)
            selections = self._select(targets, candidates, deadline, limit=remaining)
            placed = self._place(
                document,
                paragraph,
                sentences,
                selections,
                builder=builder,
                deadline=deadline,
                rejected=rejected,
            )
            added.extend(placed)

        if not added:
            raise NoResultsError(
                "No work was found that supports an uncited claim in this section."
                if searched
                else "No uncited claim in this section needed a supporting citation.",
                section_id=section_id,
                rejected=len(rejected),
            )

        checks.append(
            VerificationCheck(
                name=CheckName.PROSE_IMMUTABILITY,
                status=CheckStatus.PASSED,
                detail=f"{len(added)} citation markers inserted; no prose was regenerated.",
            )
        )
        candidate = builder.build()
        return AddCitationsResult(
            document=candidate,
            delta=compute_delta(document, candidate),
            added=added,
            checks=checks,
            rejected=rejected,
        )

    def _citable_sentences(
        self, sentences: tuple[Sentence, ...], deadline: Deadline
    ) -> list[tuple[Sentence, str]]:
        room = [sentence for sentence in sentences if _capacity(sentence) > 0]
        if not room:
            return []

        allowlist = Allowlist("sentence", {sentence.id: sentence for sentence in room})
        prompt = claims_prompt.build_prompt(
            [(sentence.id, sentence.plain_text) for sentence in room],
            get_settings().max_claims_per_paragraph,
        )
        response = self.llm.complete_structured(
            prompt, claims_prompt.ClaimExtractionResponse, remaining_seconds=deadline.remaining()
        ).value

        return [
            (allowlist.resolve(claim.sentence_id, prompt=prompt.name), claim.topic)
            for claim in response.claims
            if claim.is_citable_claim
        ]

    def _retrieve(
        self, document: Document, targets: list[tuple[Sentence, str]], deadline: Deadline
    ) -> list[Candidate]:
        cited = BibliographyIndex.of(document)
        found: dict[str, Candidate] = {}

        for sentence, topic in targets[: get_settings().max_retrieval_queries]:
            if not topic.strip() or deadline.expired:
                continue

            fresh = [
                candidate
                for candidate in self.retrieval.search(topic.strip(), deadline).candidates
                if not cited.contains(candidate.work)
            ]
            for candidate in self._rank_for(sentence, fresh, deadline):
                found.setdefault(candidate.work.identity_keys()[0], candidate)

        return [
            replace(candidate, id=f"cand_{index:03d}")
            for index, candidate in enumerate(found.values())
        ]

    def _rank_for(
        self, sentence: Sentence, candidates: list[Candidate], deadline: Deadline
    ) -> list[Candidate]:
        if not candidates:
            return []

        allowlist = Allowlist("candidate", {candidate.id: candidate for candidate in candidates})
        prompt = rerank_prompt.build_prompt(
            sentence.plain_text,
            [(candidate.id, candidate.summary()) for candidate in candidates],
            get_settings().max_resolution_candidates,
        )
        ranked = self.llm.complete_structured(
            prompt, rerank_prompt.RerankResponse, remaining_seconds=deadline.remaining()
        ).value
        return [
            allowlist.resolve(entry.candidate_id, prompt=prompt.name)
            for entry in sorted(ranked.ranked, key=lambda entry: entry.rank)
        ]

    def _select(
        self,
        targets: list[tuple[Sentence, str]],
        candidates: list[Candidate],
        deadline: Deadline,
        *,
        limit: int,
    ) -> list[tuple[Sentence, Candidate, str]]:
        if not candidates or limit <= 0:
            return []

        by_id = {candidate.id: candidate for candidate in candidates}
        allowlist = Allowlist("candidate", by_id)
        sentences = {sentence.id: sentence for sentence, _ in targets}
        sentence_allowlist = Allowlist("sentence", sentences)
        budget = {sentence.id: _capacity(sentence) for sentence, _ in targets}

        prompt = selection_prompt.build_prompt(
            [(sentence.id, sentence.plain_text) for sentence, _ in targets],
            [(candidate.id, candidate.summary()) for candidate in candidates],
            min(sum(budget.values()), limit),
        )
        response = self.llm.complete_structured(
            prompt, selection_prompt.SelectionResponse, remaining_seconds=deadline.remaining()
        ).value

        used: set[str] = set()
        selected: list[tuple[Sentence, Candidate, str]] = []
        for item in response.selections:
            sentence = sentence_allowlist.resolve(item.target_sentence_id, prompt=prompt.name)
            candidate = allowlist.resolve(item.candidate_id, prompt=prompt.name)

            if candidate.id in used or budget.get(sentence.id, 0) <= 0:
                continue
            used.add(candidate.id)
            budget[sentence.id] -= 1
            selected.append((sentence, candidate, item.rationale))
            if len(selected) >= limit:
                break
        return selected

    def _place(
        self,
        document: Document,
        paragraph: Paragraph,
        sentences: tuple[Sentence, ...],
        selections: list[tuple[Sentence, Candidate, str]],
        *,
        builder: CandidateRevisionBuilder,
        deadline: Deadline,
        rejected: list[tuple[str, str]],
    ) -> list[AddedCitation]:
        if not selections:
            return []

        tokenised = tokenize(paragraph)
        spans = {sentence.id: (sentence.char_start, sentence.char_end) for sentence in sentences}
        insertions: list[insertion.Insertion] = []
        added: list[AddedCitation] = []

        for sentence, candidate, rationale in selections:
            span = spans.get(sentence.id)
            if span is None:
                continue

            record_id, verdict = self._vet(candidate, sentence, deadline)
            if verdict not in INSERTABLE_VERDICTS:
                rejected.append((candidate.id, verdict.value))
                continue

            ordinal = self._take_ordinal()
            reference = _mint_reference(candidate, ordinal, record_id)

            if not reference.csl.is_complete_for_insertion():
                rejected.append((candidate.id, "INCOMPLETE_METADATA"))
                continue
            offset = insertion.insertion_offset(tokenised, span[0], span[1])
            neighbour = insertion.adjacent_citation(tokenised, offset)

            if neighbour is not None:
                existing = document.citations.get(neighbour)
                if existing is None:
                    continue

                builder.replace_citation(insertion.merged_node(existing, reference.id))
                citation_id = neighbour
            else:
                citation_id = f"cite_added_{ordinal:03d}"
                builder.add_citation(insertion.new_node(citation_id, reference.id))
                insertions.append(
                    insertion.Insertion(
                        sentence_id=sentence.id,
                        offset=offset,
                        citation_id=citation_id,
                        reference_id=reference.id,
                    )
                )

            builder.add_reference(reference)
            added.append(
                AddedCitation(
                    paragraph_id=paragraph.id,
                    sentence_id=sentence.id,
                    citation_id=citation_id,
                    reference=reference,
                    source_record_id=record_id,
                    verdict=verdict,
                    rationale=rationale,
                    merged_into=neighbour,
                )
            )

        if insertions:
            revised = insertion.apply_insertions(tokenised, insertions)
            insertion.assert_prose_unchanged(tokenised, revised, paragraph.id)
            builder.replace_paragraph(paragraph.id, revised)
        return added

    def _vet(
        self, candidate: Candidate, sentence: Sentence, deadline: Deadline
    ) -> tuple[str, SupportVerdict]:
        record_id = self.sources.snapshot(candidate.work)
        if not candidate.work.has_abstract or candidate.work.abstract is None:
            return record_id, SupportVerdict.EVIDENCE_UNAVAILABLE

        spans = split_abstract(candidate.work.abstract)
        if not spans:
            return record_id, SupportVerdict.EVIDENCE_UNAVAILABLE

        allowlist = Allowlist("span", {span.id: span for span in spans})
        prompt = support_prompt.build_prompt(sentence.plain_text, span_listing(spans))
        response = self.llm.complete_structured(
            prompt, support_prompt.SupportResponse, remaining_seconds=deadline.remaining()
        ).value
        allowlist.resolve_all(response.evidence_span_ids, prompt=prompt.name)

        chosen = require_choice(
            response.verdict,
            [verdict.value for verdict in support_prompt.MODEL_SELECTABLE_VERDICTS],
            prompt=prompt.name,
            field="verdict",
        )
        return record_id, SupportVerdict(chosen)

    def _take_ordinal(self) -> int:
        ordinal = self._next_ordinal
        self._next_ordinal += 1
        return ordinal


def _mint_reference(candidate: Candidate, ordinal: int, source_record_id: str) -> ReferenceRecord:
    reference_id = f"ref_added_{ordinal:03d}"
    return ReferenceRecord(
        id=reference_id,
        csl=candidate.work.to_csl(reference_id),
        raw_text="",
        resolution_method=ResolutionMethod.EXTERNAL_ID,
        resolution_confidence=ResolutionConfidence.CERTAIN,
        source_record_id=source_record_id,
    )


def _next_added_ordinal(document: Document) -> int:
    used = [
        int(identifier.rsplit("_", 1)[-1])
        for identifier in [*document.citations, *(r.id for r in document.references)]
        if "_added_" in identifier and identifier.rsplit("_", 1)[-1].isdigit()
    ]
    return max(used, default=0) + 1


def _capacity(sentence: Sentence) -> int:
    return max(0, get_settings().max_citations_per_claim - len(sentence.citation_ids))
