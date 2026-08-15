"""ADD_SUPPORTING_CITATIONS.

The intent with the most obvious way to go wrong: a model that suggests a paper
which does not exist, or quietly rewrites the sentence while adding a marker to
it. Both are tested here by making the scripted model attempt exactly that.

The load-bearing assertion in this file is the prose one. Everything else is a
policy that could reasonably be argued about; "the sentence the researcher wrote
is the sentence that remains" is not negotiable, and it is checked by comparing
the paragraphs character for character with the markers removed.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from app.domain.delta import ChangeKind
from app.domain.document import Document
from app.domain.errors import GroundingValidationError, NoResultsError, ProseMutationError
from app.domain.source import ProviderName, ProviderWork
from app.providers.llm import Prompt
from app.services.deadline import Deadline
from app.services.editor import insertion
from app.services.editor.addcite import CitationAdder
from app.services.parser.segmenter import detokenize, segment_paragraph, tokenize
from app.services.research.retrieval import (
    AcademicRetrievalService,
    Candidate,
    VerifiedCandidateSet,
)
from app.settings import get_settings
from tests.conftest import validated
from tests.support.llm import ScriptedLLM

WORK = ProviderWork(
    provider=ProviderName.OPENALEX,
    external_id="W2741809807",
    title="Scaling Laws for Neural Language Models",
    doi="10.5555/3295222.3295349",
    year=2017,
    authors=("Ashish Vaswani", "Noam Shazeer"),
    venue="NeurIPS",
    abstract=(
        "The dominant sequence transduction models are based on complex recurrent or "
        "convolutional neural networks. We propose the Transformer, based solely on "
        "attention mechanisms. Experiments show these models are superior in quality "
        "while being more parallelizable and requiring significantly less time to train."
    ),
    url="https://example.org/work",
)


@pytest.fixture(scope="module")
def document() -> Document:
    return validated("A_numeric").document


def longest_section(document: Document) -> Any:
    return max(document.sections, key=lambda section: section.word_count())


class FakeRetrieval(AcademicRetrievalService):
    """Returns fixed works without touching a provider.

    Subclassed rather than duck-typed so that a change to the real ``search``
    signature breaks this file instead of silently diverging from it.
    """

    def __init__(self, works: list[ProviderWork]) -> None:
        self.works = works
        self.queries: list[str] = []

    def search(
        self, query: str, deadline: Deadline, *, limit: int | None = None
    ) -> VerifiedCandidateSet:
        self.queries.append(query)
        result = VerifiedCandidateSet(query=query)
        for index, work in enumerate(self.works):
            identifier = f"cand_{index:03d}"
            result.candidates.append(
                Candidate(id=identifier, work=work, csl=work.to_csl(identifier))
            )
        return result


class FakeSources:
    def __init__(self) -> None:
        self.snapshots: list[ProviderWork] = []

    def snapshot(self, work: ProviderWork) -> str:
        self.snapshots.append(work)
        return f"src_{len(self.snapshots):03d}"


def scripted(
    *,
    verdict: str = "SUPPORTED",
    candidate_id: str = "cand_000",
    sentence_id: str | None = None,
) -> ScriptedLLM:
    """A model that behaves: it picks the first uncited sentence and one work."""

    def claims(prompt: Prompt) -> dict[str, Any]:
        return {
            "claims": [
                {
                    "sentence_id": _first_id(prompt.user),
                    "is_citable_claim": True,
                    "topic": "transformer architectures for sequence modelling",
                }
            ]
        }

    def selection(prompt: Prompt) -> dict[str, Any]:
        return {
            "selections": [
                {
                    "target_sentence_id": sentence_id or _first_id(prompt.user),
                    "candidate_id": candidate_id,
                    "rationale": "Introduces the architecture the sentence describes.",
                }
            ]
        }

    def support(prompt: Prompt) -> dict[str, Any]:
        return {
            "verdict": verdict,
            "evidence_span_ids": [_first_id(prompt.user)],
            "reason": "The abstract states the result the sentence describes.",
        }

    return ScriptedLLM(
        {
            "claims": claims,
            "rerank": {"ranked": [{"candidate_id": candidate_id, "rank": 1, "rationale": "x"}]},
            "selection": selection,
            "support": support,
        }
    )


_LISTED_ID = re.compile(r"^(\S+):\s", re.MULTILINE)


def _first_id(user_prompt: str) -> str:
    """The first identifier the server listed in the prompt.

    Scripted models pick from what they were offered, exactly as a real one must.
    """
    match = _LISTED_ID.search(user_prompt)
    if match is None:
        raise AssertionError("the prompt listed no identifiers")
    return match.group(1)


def run(document: Document, llm: ScriptedLLM, works: list[ProviderWork] | None = None) -> Any:
    adder = CitationAdder(
        llm=llm,
        retrieval=FakeRetrieval(works if works is not None else [WORK]),
        sources=FakeSources(),
    )
    return adder.run(document, longest_section(document).id, Deadline.after("test", 60.0))


def test_a_marker_goes_before_the_full_stop() -> None:
    text = "Transformers dominate sequence modelling."
    offset = insertion.insertion_offset(text, 0, len(text))

    assert text[:offset] + " [[CITE:c1]]" + text[offset:] == (
        "Transformers dominate sequence modelling [[CITE:c1]]."
    )


def test_a_marker_goes_inside_a_closing_quote() -> None:
    """``as shown [1]."`` is the convention; ``as shown." [1]`` is not."""
    text = 'The authors call this "attention."'
    offset = insertion.insertion_offset(text, 0, len(text))

    assert text[offset:] == '."'


def test_an_adjacent_citation_is_joined_rather_than_doubled() -> None:
    """``[3, 7]``, never ``[3][7]``."""
    text = "Transformers dominate sequence modelling [[CITE:c1]]."
    offset = insertion.insertion_offset(text, 0, len(text))

    assert insertion.adjacent_citation(text, offset) == "c1"


def test_an_unrelated_citation_earlier_in_the_sentence_is_not_joined() -> None:
    text = "Earlier work [[CITE:c1]] established the baseline that later systems improved on."
    offset = insertion.insertion_offset(text, 0, len(text))

    assert insertion.adjacent_citation(text, offset) is None


def test_prose_equality_ignores_markers_but_nothing_else() -> None:
    insertion.assert_prose_unchanged("A claim.", "A claim [[CITE:c1]].", "p_1_1")

    with pytest.raises(ProseMutationError):
        insertion.assert_prose_unchanged("A claim.", "A different claim [[CITE:c1]].", "p_1_1")


def test_a_citation_is_added_and_the_reference_is_minted(document: Document) -> None:
    result = run(document, scripted())

    assert result.added
    added = result.added[0]
    assert added.reference.id.startswith("ref_added_")

    assert added.reference.csl.id == added.reference.id
    assert result.delta.of_kind(ChangeKind.CITATION_ADDED)


def test_no_prose_is_regenerated(document: Document) -> None:
    """The guarantee the whole intent rests on.

    Every paragraph in the candidate must be character-identical to the original
    once markers are stripped -- not merely similar, and not merely unflagged by
    the delta.
    """
    result = run(document, scripted())

    before = {paragraph.id: tokenize(paragraph) for paragraph in document.paragraphs()}
    for paragraph in result.document.paragraphs():
        after = tokenize(paragraph)

        assert _words(after) == _words(before[paragraph.id])
        insertion.assert_prose_unchanged(before[paragraph.id], after, paragraph.id)


def _words(tokenised: str) -> list[str]:
    return re.findall(r"\w+", detokenize(tokenised))


def test_the_sentence_that_was_chosen_is_the_sentence_that_gains_the_marker(
    document: Document,
) -> None:
    result = run(document, scripted())
    added = result.added[0]

    paragraph = result.document.paragraph(added.paragraph_id)
    assert paragraph is not None
    sentence = next(item for item in segment_paragraph(paragraph) if item.id == added.sentence_id)
    assert added.citation_id in sentence.citation_ids


def test_an_invented_candidate_id_is_refused(document: Document) -> None:
    """The reranker returning a work that was never retrieved is the failure this
    system exists to prevent, so it raises rather than being dropped."""
    with pytest.raises(GroundingValidationError):
        run(document, scripted(candidate_id="cand_hallucinated"))


def test_an_invented_sentence_id_is_refused(document: Document) -> None:
    with pytest.raises(GroundingValidationError):
        run(document, scripted(sentence_id="s_p_99_9_1"))


def test_a_contradicting_work_is_never_offered(document: Document) -> None:
    """Vetting happens before insertion, so a contradicted suggestion does not
    reach the researcher at all."""
    with pytest.raises(NoResultsError):
        run(document, scripted(verdict="CONTRADICTED"))


def test_finding_nothing_raises_no_results_rather_than_an_empty_edit(
    document: Document,
) -> None:
    """Distinct from a provider failure, and distinct from an empty proposal."""
    with pytest.raises(NoResultsError) as caught:
        run(document, scripted(), works=[])

    assert caught.value.code.value == "NO_RESULTS"


def test_a_work_too_incomplete_to_cite_is_rejected(document: Document) -> None:
    """A suggestion the reader cannot look up is a gesture at a citation."""
    bare = ProviderWork(
        provider=ProviderName.OPENALEX,
        external_id="W1",
        title="",
        abstract=WORK.abstract,
    )

    with pytest.raises(NoResultsError):
        run(document, scripted(), works=[bare])


def test_the_search_query_is_the_topic_not_the_manuscript(document: Document) -> None:
    """Posting unpublished prose to two third-party APIs is not what "add some
    citations" asks for."""
    retrieval = FakeRetrieval([WORK])
    CitationAdder(llm=scripted(), retrieval=retrieval, sources=FakeSources()).run(
        document, longest_section(document).id, Deadline.after("test", 60.0)
    )

    body = " ".join(
        detokenize(tokenize(paragraph)) for paragraph in longest_section(document).paragraphs
    )
    assert retrieval.queries
    for query in retrieval.queries:
        assert query not in body


def test_add_citations_has_an_operation_wide_fanout_bound(
    document: Document, monkeypatch: Any
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "max_add_citation_paragraphs", 1)
    monkeypatch.setattr(settings, "max_added_citations_per_operation", 1)
    llm = scripted()

    result = run(document, llm)

    assert len(result.added) == 1
    assert llm.calls.count("claims") == 1
    assert llm.calls.count("rerank") == 1
    assert llm.calls.count("selection") == 1
    assert llm.calls.count("support") == 1


def test_the_evidence_behind_a_suggestion_is_snapshotted(document: Document) -> None:
    sources = FakeSources()
    CitationAdder(llm=scripted(), retrieval=FakeRetrieval([WORK]), sources=sources).run(
        document, longest_section(document).id, Deadline.after("test", 60.0)
    )

    assert sources.snapshots


def test_blocks_and_existing_references_survive(document: Document) -> None:
    result = run(document, scripted())

    assert result.document.blocks == document.blocks
    assert not result.delta.touches_blocks
    assert len(result.document.references) == len(document.references) + len(result.added)


def other_work(external_id: str, title: str) -> ProviderWork:
    return ProviderWork(
        provider=ProviderName.OPENALEX,
        external_id=external_id,
        title=title,
        year=2020,
        authors=("Grace Hopper",),
        doi=f"10.5/{external_id}",
        abstract=WORK.abstract,
        url=f"https://openalex.org/{external_id}",
    )


def test_each_claim_is_ranked_against_itself(document: Document) -> None:
    """Ranking a pooled candidate set against one claim's text is the bug where a
    work retrieved for the third sentence dies on the first sentence's relevance.

    The reranker is therefore called once per claim, and each call must carry
    that claim's own text.
    """
    claim_texts: list[str] = []

    def claims(prompt: Prompt) -> dict[str, Any]:
        return {
            "claims": [
                {
                    "sentence_id": sentence_id,
                    "is_citable_claim": True,
                    "topic": f"topic for {sentence_id}",
                }
                for sentence_id in _listed_ids(prompt.user)[:2]
            ]
        }

    def rerank(prompt: Prompt) -> dict[str, Any]:
        claim_texts.append(prompt.user)
        return {"ranked": [{"candidate_id": "cand_000", "rank": 1, "rationale": "x"}]}

    llm = ScriptedLLM(
        {
            "claims": claims,
            "rerank": rerank,
            "selection": {"selections": []},
            "support": {
                "verdict": "SUPPORTED",
                "evidence_span_ids": ["span_000"],
                "reason": "r",
            },
        }
    )
    adder = CitationAdder(llm=llm, retrieval=FakeRetrieval([WORK]), sources=FakeSources())
    with pytest.raises(NoResultsError):
        adder.run(document, longest_section(document).id, Deadline.after("test", 60.0))

    assert len(claim_texts) > 1, "the reranker was called once for several claims"
    assert len(set(claim_texts)) == len(claim_texts), "two claims were ranked on the same text"


def test_a_cited_claim_can_still_take_another_source(document: Document) -> None:
    """ "Add more citations to the introduction" is a request about cited claims.

    Refusing every sentence that already has one would make the command return
    nothing on any introduction that was cited at all.
    """
    section = longest_section(document)
    cited = [
        sentence
        for paragraph in section.paragraphs
        for sentence in segment_paragraph(paragraph)
        if sentence.citation_ids
    ]
    if not cited:
        pytest.skip("this corpus section has no cited sentence")

    target = cited[0]

    def claims(prompt: Prompt) -> dict[str, Any]:
        offered = _listed_ids(prompt.user)
        if target.id not in offered:
            return {"claims": []}
        return {
            "claims": [{"sentence_id": target.id, "is_citable_claim": True, "topic": "the topic"}]
        }

    llm = ScriptedLLM(
        {
            "claims": claims,
            "rerank": {"ranked": [{"candidate_id": "cand_000", "rank": 1, "rationale": "x"}]},
            "selection": {
                "selections": [
                    {
                        "target_sentence_id": target.id,
                        "candidate_id": "cand_000",
                        "rationale": "adds a second source",
                    }
                ]
            },
            "support": {
                "verdict": "SUPPORTED",
                "evidence_span_ids": ["span_000"],
                "reason": "r",
            },
        }
    )
    result = run(document, llm)

    assert [added.sentence_id for added in result.added] == [target.id]


def test_one_claim_never_takes_more_than_its_cap(document: Document) -> None:
    """The prompt states the limit; this enforces it on what came back."""
    works = [other_work(f"W{index}", f"A work {index}") for index in range(4)]

    def claims(prompt: Prompt) -> dict[str, Any]:
        return {
            "claims": [
                {
                    "sentence_id": _first_id(prompt.user),
                    "is_citable_claim": True,
                    "topic": "the topic",
                }
            ]
        }

    def selection(prompt: Prompt) -> dict[str, Any]:
        sentence_id = _first_id(prompt.user)
        return {
            "selections": [
                {
                    "target_sentence_id": sentence_id,
                    "candidate_id": f"cand_{index:03d}",
                    "rationale": "greedy",
                }
                for index in range(len(works))
            ]
        }

    llm = ScriptedLLM(
        {
            "claims": claims,
            "rerank": lambda prompt: {
                "ranked": [
                    {"candidate_id": candidate_id, "rank": rank + 1, "rationale": "x"}
                    for rank, candidate_id in enumerate(_listed_ids(prompt.user))
                ]
            },
            "selection": selection,
            "support": {
                "verdict": "SUPPORTED",
                "evidence_span_ids": ["span_000"],
                "reason": "r",
            },
        }
    )
    result = run(document, llm, works=works)

    per_sentence: dict[str, int] = {}
    for added in result.added:
        per_sentence[added.sentence_id] = per_sentence.get(added.sentence_id, 0) + 1
    assert max(per_sentence.values()) <= get_settings().max_citations_per_claim


def test_a_work_already_in_the_bibliography_is_not_offered_again(document: Document) -> None:
    """Minting a second entry for a work the author already cites would put a
    duplicate in their reference list."""
    cited = next(r for r in document.references if r.csl.title)
    duplicate = other_work("W_dup", cited.csl.title or "").model_copy(update={"doi": cited.csl.DOI})

    with pytest.raises(NoResultsError):
        run(document, scripted(), works=[duplicate])


def _listed_ids(user_prompt: str) -> list[str]:
    return _LISTED_ID.findall(user_prompt)
