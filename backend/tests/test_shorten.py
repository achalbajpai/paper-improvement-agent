"""SHORTEN_SECTION and its safety spine.

The tests here are mostly adversarial: each one describes a specific way a
shortening model can damage a manuscript, and asserts that the damage is refused
rather than reported. The scripted model is used to *produce* the damage on
purpose -- a stand-in that always behaves well would test nothing.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.domain.delta import ChangeKind
from app.domain.document import Document
from app.domain.errors import (
    CandidateConstructionError,
    ProtectedTokenError,
    UnsupportedNoveltyError,
)
from app.providers.llm import Prompt
from app.services.deadline import Deadline
from app.services.editor import tokens
from app.services.editor.candidate_revision import CandidateRevisionBuilder
from app.services.editor.delta_engine import compute_delta
from app.services.editor.planner import MIN_PARAGRAPH_WORDS, plan_shortening
from app.services.editor.shorten import shorten_section
from app.services.parser.segmenter import detokenize, tokenize
from app.settings import get_settings
from tests.conftest import validated
from tests.support.llm import ScriptedLLM


@pytest.fixture(scope="module")
def document() -> Document:
    return validated("A_numeric").document


def longest_section(document: Document) -> Any:
    return max(document.sections, key=lambda section: section.word_count())


def test_dropping_a_citation_is_allowed_and_recorded() -> None:
    """Removal is the point of shortening; it is policed later, not here."""
    audit = tokens.audit("A [[CITE:c1]] and B [[CITE:c2]].", "A [[CITE:c1]].", "p_1_1")

    assert audit.kept == ("c1",)
    assert audit.dropped == ("c2",)


def test_reordering_is_allowed() -> None:
    audit = tokens.audit("A [[CITE:c1]] B [[CITE:c2]].", "B [[CITE:c2]] A [[CITE:c1]].", "p_1_1")

    assert audit.reordered
    assert audit.dropped == ()


def test_duplicating_a_token_is_refused() -> None:
    """One occurrence became two assertions of support.

    Silently de-duplicating would mean guessing which occurrence the model meant.
    """
    with pytest.raises(ProtectedTokenError) as caught:
        tokens.audit("A [[CITE:c1]].", "A [[CITE:c1]] and again [[CITE:c1]].", "p_1_1")

    assert caught.value.details["token_ids"] == ["c1"]


def test_inventing_a_token_is_refused() -> None:
    with pytest.raises(ProtectedTokenError):
        tokens.audit("A [[CITE:c1]].", "A [[CITE:c1]] and [[CITE:c_invented]].", "p_1_1")


def test_a_mangled_token_is_refused_rather_than_read_as_a_removal() -> None:
    """``[[CITE:c1`` parses as no token at all, so counting well-formed tokens
    would report this as a clean drop."""
    with pytest.raises(ProtectedTokenError):
        tokens.audit("A [[CITE:c1]].", "A [[CITE:c1.", "p_1_1")


def test_rehydrate_strips_an_unknown_token_rather_than_emitting_it() -> None:
    stripped = tokens.rehydrate("A [[CITE:c1]] B [[CITE:c9]].", frozenset({"c1"}))

    assert "[[CITE:c1]]" in stripped
    assert "c9" not in stripped


def test_the_plan_spreads_the_reduction_across_paragraphs(document: Document) -> None:
    plan = plan_shortening(longest_section(document), 0.25)

    assert len(plan.targets) >= 1
    for target in plan.targets:
        assert target.target_words < target.words_before


def test_short_paragraphs_are_skipped_with_a_reason(document: Document) -> None:
    """Below the floor, "shorten this" becomes "delete a sentence"."""
    section = longest_section(document)
    plan = plan_shortening(section, 0.25)

    skipped_ids = {paragraph_id for paragraph_id, _ in plan.skipped}
    for paragraph in section.paragraphs:
        if paragraph.word_count() < MIN_PARAGRAPH_WORDS:
            assert paragraph.id in skipped_ids


def test_an_unshortenable_section_raises_rather_than_producing_an_empty_plan(
    document: Document,
) -> None:
    tiny = min(document.sections, key=lambda section: section.word_count())
    if tiny.word_count() >= MIN_PARAGRAPH_WORDS * 2:
        pytest.skip("this corpus has no section too small to shorten")
    with pytest.raises(CandidateConstructionError):
        plan_shortening(tiny, 0.25)


def test_the_ratio_is_clamped_so_shortening_never_becomes_deletion(
    document: Document,
) -> None:
    plan = plan_shortening(longest_section(document), 0.95)
    assert plan.ratio <= 0.5


@pytest.fixture
def generative(monkeypatch: Any) -> None:
    """Pin generative rewriting on.

    Extractive-only is the default until a live model-safety go/no-go passes for the
    configured model, so a test about the rewriter -- and about the checks that
    exist precisely because a rewriter can invent -- has to ask for the mode
    those checks run in.
    """
    monkeypatch.setattr(get_settings(), "shorten_extractive_only", False)


def scripted_shortener(transform: Any) -> ScriptedLLM:
    """A model that shortens by applying ``transform`` to the tokenised text."""

    def rewrite(prompt: Prompt) -> dict[str, Any]:
        return {"revised_text": transform(_body(prompt.user)), "claimed_actions": ["shortened"]}

    return ScriptedLLM(
        {
            "rewrite": rewrite,
            "novelty": {"has_unsupported_novelty": False, "findings": []},
        }
    )


def _body(user_prompt: str) -> str:
    """Recover the delimited paragraph the server put in the prompt."""
    start = user_prompt.index("<<<BEGIN_UNTRUSTED_CONTENT>>>") + len(
        "<<<BEGIN_UNTRUSTED_CONTENT>>>"
    )
    end = user_prompt.index("<<<END_UNTRUSTED_CONTENT>>>")
    return user_prompt[start:end].strip()


def halve(text: str) -> str:
    """Keep the first half of the sentences, tokens intact."""
    parts = text.split(". ")
    keep = max(1, len(parts) // 2)
    return ". ".join(parts[:keep]).rstrip(".") + "."


def test_a_clean_shortening_produces_a_candidate_and_a_delta(
    document: Document, generative: None
) -> None:
    section = longest_section(document)
    result = shorten_section(
        document,
        section.id,
        llm=scripted_shortener(halve),
        deadline=Deadline.after("test", 60.0),
        ratio=0.25,
    )

    assert result.document.word_count() < document.word_count()
    assert result.delta.changed_paragraph_ids
    assert not result.delta.touches_blocks


def test_paragraph_ids_survive_a_rewrite(document: Document, generative: None) -> None:
    """Every anchor, finding, and delta refers to a paragraph by id."""
    section = longest_section(document)
    result = shorten_section(
        document,
        section.id,
        llm=scripted_shortener(halve),
        deadline=Deadline.after("test", 60.0),
    )

    before = {paragraph.id for paragraph in document.paragraphs()}
    after = {paragraph.id for paragraph in result.document.paragraphs()}
    assert before == after


def test_references_and_blocks_are_untouched_by_shortening(
    document: Document, generative: None
) -> None:
    result = shorten_section(
        document,
        longest_section(document).id,
        llm=scripted_shortener(halve),
        deadline=Deadline.after("test", 60.0),
    )

    assert result.document.references == document.references
    assert result.document.blocks == document.blocks
    assert not result.delta.of_kind(ChangeKind.REFERENCE_REMOVED)


def test_a_dropped_citation_is_reported_not_hidden(document: Document, generative: None) -> None:
    def drop_citations(text: str) -> str:
        return detokenize(text)

    result = shorten_section(
        document,
        longest_section(document).id,
        llm=scripted_shortener(drop_citations),
        deadline=Deadline.after("test", 60.0),
    )

    assert result.dropped_citations
    assert result.delta.removed_citation_ids


def test_an_invented_citation_stops_the_whole_section(document: Document, generative: None) -> None:
    def invent(text: str) -> str:
        return f"{text} See also [[CITE:cite_added_999]]."

    with pytest.raises(ProtectedTokenError):
        shorten_section(
            document,
            longest_section(document).id,
            llm=scripted_shortener(invent),
            deadline=Deadline.after("test", 60.0),
        )


def test_unsupported_novelty_blocks_and_is_never_a_warning(
    document: Document, generative: None
) -> None:
    """A researcher cannot acknowledge a fabricated statistic: acknowledging it
    would require already knowing it was fabricated."""
    llm = ScriptedLLM(
        {
            "rewrite": lambda prompt: {
                "revised_text": _body(prompt.user),
                "claimed_actions": [],
            },
            "novelty": {
                "has_unsupported_novelty": True,
                "findings": [{"assertion": "reduces latency by 30%", "kind": "NEW_NUMBER"}],
            },
        }
    )

    with pytest.raises(UnsupportedNoveltyError):
        shorten_section(
            document,
            longest_section(document).id,
            llm=llm,
            deadline=Deadline.after("test", 60.0),
        )


def test_a_failure_stages_nothing(document: Document, generative: None) -> None:
    """All-or-nothing across paragraphs: a partially shortened section is a
    document nobody asked for and nobody reviewed."""
    section = longest_section(document)
    calls: list[int] = []

    def fail_on_second(text: str) -> str:
        calls.append(1)
        if len(calls) > 1:
            return f"{text} [[CITE:cite_added_999]]"
        return halve(text)

    before = document.model_dump(mode="json")
    with pytest.raises(ProtectedTokenError):
        shorten_section(
            document,
            section.id,
            llm=scripted_shortener(fail_on_second),
            deadline=Deadline.after("test", 60.0),
        )

    assert document.model_dump(mode="json") == before


def test_extractive_mode_invents_nothing(document: Document, monkeypatch: Any) -> None:
    """The model-safety go/no-go switch. Sentences are removed whole, so there is no
    text for a model to invent."""
    monkeypatch.setattr(get_settings(), "shorten_extractive_only", True)
    section = longest_section(document)

    result = shorten_section(
        document,
        section.id,
        llm=ScriptedLLM({}),
        deadline=Deadline.after("test", 60.0),
        ratio=0.3,
    )

    assert result.extractive_only
    for paragraph in result.document.paragraphs():
        original = document.paragraph(paragraph.id)
        assert original is not None
        for word in paragraph.text.split():
            assert word in original.text


def test_extractive_mode_keeps_cited_sentences(document: Document, monkeypatch: Any) -> None:
    """Dropping a cited sentence would remove the author's support, and this mode
    runs precisely when the safety net for a rewrite is unavailable."""
    monkeypatch.setattr(get_settings(), "shorten_extractive_only", True)
    section = longest_section(document)

    result = shorten_section(
        document,
        section.id,
        llm=ScriptedLLM({}),
        deadline=Deadline.after("test", 60.0),
        ratio=0.4,
    )

    assert result.delta.removed_citation_ids == ()


def test_the_builder_refuses_a_citation_that_does_not_exist(document: Document) -> None:
    builder = CandidateRevisionBuilder(document)
    paragraph = next(p for p in document.paragraphs() if p.citation_ids)

    with pytest.raises(CandidateConstructionError):
        builder.replace_paragraph(paragraph.id, "Some prose [[CITE:cite_imaginary]].")


def test_the_builder_reattaches_citations_by_identity(document: Document) -> None:
    paragraph = next(p for p in document.paragraphs() if len(p.citation_ids) >= 2)
    reversed_text = " ".join(f"[[CITE:{cid}]]" for cid in reversed(paragraph.citation_ids))

    builder = CandidateRevisionBuilder(document)
    builder.replace_paragraph(paragraph.id, f"Prose. {reversed_text}")
    candidate = builder.build()

    rebuilt = candidate.paragraph(paragraph.id)
    assert rebuilt is not None
    assert rebuilt.citation_ids == tuple(reversed(paragraph.citation_ids))


def test_an_edit_that_changes_nothing_is_refused(document: Document) -> None:
    with pytest.raises(CandidateConstructionError):
        CandidateRevisionBuilder(document).build()


def test_the_delta_is_computed_from_documents_not_from_claims(document: Document) -> None:
    """A model reporting "removed two sentences" while also dropping a citation
    is the failure this engine exists to catch.

    The prose here is untouched and only the markers are gone, so the engine
    reports citation removals and *not* a text change -- which is the
    distinction a researcher reading the diff actually needs.
    """
    paragraph = next(p for p in document.paragraphs() if p.citation_ids)
    builder = CandidateRevisionBuilder(document)
    builder.replace_paragraph(paragraph.id, detokenize(tokenize(paragraph)))
    candidate = builder.build()

    delta = compute_delta(document, candidate)

    assert set(delta.removed_citation_ids) == set(paragraph.citation_ids)
    assert paragraph.id not in delta.changed_paragraph_ids
    for change in delta.of_kind(ChangeKind.CITATION_REMOVED):
        assert change.related_ids == (paragraph.id,)


def test_an_identical_document_produces_an_empty_delta(document: Document) -> None:
    assert compute_delta(document, document).is_empty


def test_a_removed_block_is_reported(document: Document) -> None:
    """No supported edit touches a block, so any difference is a bug in the
    editing path rather than a decision to put to the researcher."""
    block_id = next(iter(document.blocks))
    stripped = document.model_copy(
        update={"blocks": {k: v for k, v in document.blocks.items() if k != block_id}}
    )

    delta = compute_delta(document, stripped)

    assert delta.touches_blocks
    assert delta.of_kind(ChangeKind.BLOCK_REMOVED)[0].subject_id == block_id


def test_a_named_paragraph_is_shortened_alone(document: Document) -> None:
    """A command that named one paragraph must not rewrite the section around it.

    The routed paragraph id used to be validated and then dropped, so asking to
    shorten one paragraph produced a diff over every eligible paragraph in its
    section -- the narrower the command, the wider the edit.
    """
    section = longest_section(document)
    wide = plan_shortening(section, 0.25)
    assert len(wide.targets) > 1, "this section cannot show the difference"

    chosen = wide.targets[0].paragraph_id
    narrow = plan_shortening(section, 0.25, chosen)

    assert [target.paragraph_id for target in narrow.targets] == [chosen]
    assert narrow.words_before < wide.words_before


def test_naming_an_ineligible_paragraph_says_which_one_and_why(document: Document) -> None:
    section = next(
        (
            s
            for s in document.sections
            if any(p.word_count() < MIN_PARAGRAPH_WORDS for p in s.paragraphs)
        ),
        None,
    )
    if section is None:
        pytest.skip("this corpus has no paragraph below the floor")
    tiny = next(p for p in section.paragraphs if p.word_count() < MIN_PARAGRAPH_WORDS)

    with pytest.raises(CandidateConstructionError) as caught:
        plan_shortening(section, 0.25, tiny.id)

    assert caught.value.details["paragraph_id"] == tiny.id
    assert "words" in str(caught.value)


def test_naming_a_paragraph_from_another_section_is_refused(document: Document) -> None:
    section = longest_section(document)
    outsider = next(p for p in document.paragraphs() if p not in section.paragraphs)

    with pytest.raises(CandidateConstructionError):
        plan_shortening(section, 0.25, outsider.id)


def test_the_delta_reports_the_edit_against_the_paragraphs_it_changed(
    document: Document,
) -> None:
    """The scoped counts are the edit's own size; the document counts are not.

    Reporting the manuscript total as the edit's denominator is how a trim to one
    paragraph gets displayed as though the whole paper had been rewritten.
    """
    paragraph = next(p for p in document.paragraphs() if p.word_count() >= MIN_PARAGRAPH_WORDS)
    builder = CandidateRevisionBuilder(document)
    builder.replace_paragraph(paragraph.id, f"Trimmed. {tokenize(paragraph)}"[:80])
    candidate = builder.build()

    delta = compute_delta(document, candidate)
    revised = candidate.paragraph(paragraph.id)
    assert revised is not None

    assert delta.scope_words_before == paragraph.word_count()
    assert delta.scope_words_after == revised.word_count()

    assert delta.words_before == document.word_count()
    assert delta.scope_words_before < delta.words_before

    assert (
        delta.words_before - delta.words_after == delta.scope_words_before - delta.scope_words_after
    )


def test_an_edit_that_changes_no_prose_reports_no_word_change(document: Document) -> None:
    """Adding citations leaves every paragraph's prose alone.

    Scoped counts of zero are the honest answer here, and the summary says so in
    words rather than printing "0 -> 0".
    """
    paragraph = next(p for p in document.paragraphs() if p.citation_ids)
    builder = CandidateRevisionBuilder(document)
    builder.replace_paragraph(paragraph.id, detokenize(tokenize(paragraph)))
    candidate = builder.build()

    delta = compute_delta(document, candidate)

    assert delta.scope_words_before == 0
    assert delta.scope_words_after == 0
    assert "No prose changed." in delta.summary()
