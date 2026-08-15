from __future__ import annotations

import pytest

from app.domain.citation import CitationItem
from app.domain.document import Document, Paragraph
from app.domain.errors import IdentityInvariantError
from app.domain.reference import CSLItem, ReferenceRecord
from app.domain.review import ProviderDegradation, SupportVerdict
from app.domain.verification import BlockerCode, CheckName, CheckStatus, WarningCode
from app.services.editor.addcite import AddedCitation
from app.services.editor.candidate_revision import CandidateRevisionBuilder
from app.services.editor.context import changed_contexts, contexts_for
from app.services.editor.delta_engine import compute_delta
from app.services.editor.verification import VerificationInputs, verify
from app.services.parser.segmenter import detokenize, tokenize
from tests.conftest import validated


@pytest.fixture(scope="module")
def document() -> Document:
    return validated("A_numeric").document


def outcome(base: Document, candidate: Document, **kwargs: object) -> object:
    return verify(
        VerificationInputs(
            base=base,
            candidate=candidate,
            delta=compute_delta(base, candidate),
            **kwargs,
        )
    )


def cited_paragraph(document: Document) -> Paragraph:
    return next(
        paragraph for paragraph in document.paragraphs() if len(paragraph.citation_ids) >= 1
    )


def test_moving_a_citation_does_not_trigger_re_verification(document: Document) -> None:
    paragraph = cited_paragraph(document)
    sentences = paragraph.text.split(". ")
    if len(sentences) < 2:
        pytest.skip("this paragraph has only one sentence")

    before = contexts_for(document)
    builder = CandidateRevisionBuilder(document)

    builder.replace_paragraph(paragraph.id, "A new opening sentence. " + tokenize(paragraph))
    after = contexts_for(builder.build())

    assert changed_contexts(before, after) == ()


def test_rewording_the_host_sentence_does_trigger_re_verification(
    document: Document,
) -> None:
    paragraph = cited_paragraph(document)
    citation_id = paragraph.citation_ids[0]

    before = contexts_for(document)
    builder = CandidateRevisionBuilder(document)
    builder.replace_paragraph(
        paragraph.id, f"A completely different claim entirely [[CITE:{citation_id}]]."
    )
    after = contexts_for(builder.build())

    assert citation_id in changed_contexts(before, after)


def test_an_unverifiable_attachment_is_never_recorded_as_a_pass(document: Document) -> None:
    paragraph = cited_paragraph(document)
    citation_id = paragraph.citation_ids[0]
    builder = CandidateRevisionBuilder(document)
    builder.replace_paragraph(paragraph.id, f"Something else entirely [[CITE:{citation_id}]].")
    candidate = builder.build()

    result = verify(
        VerificationInputs(
            base=document, candidate=candidate, delta=compute_delta(document, candidate)
        )
    )

    attachment = next(
        check for check in result.checks if check.name is CheckName.SEMANTIC_ATTACHMENT
    )
    assert attachment.status is CheckStatus.NOT_RUN
    assert any(warning.code is WarningCode.ATTACHMENT_INVALIDATED for warning in result.warnings)


def test_a_removed_citation_warns_with_the_marker_the_author_wrote(
    document: Document,
) -> None:
    paragraph = cited_paragraph(document)
    citation_id = paragraph.citation_ids[0]
    builder = CandidateRevisionBuilder(document)
    builder.replace_paragraph(paragraph.id, detokenize(tokenize(paragraph)))
    candidate = builder.build()

    result = verify(
        VerificationInputs(
            base=document, candidate=candidate, delta=compute_delta(document, candidate)
        )
    )

    removals = [
        warning for warning in result.warnings if warning.code is WarningCode.CITATION_REMOVED
    ]
    assert citation_id in {subject for warning in removals for subject in warning.subject_ids}
    assert result.can_accept, "removal is a consequence to accept, not a refusal"


def test_a_reference_left_uncited_is_retained_and_reported(document: Document) -> None:
    builder = CandidateRevisionBuilder(document)
    for paragraph in document.paragraphs():
        if paragraph.citation_ids:
            builder.replace_paragraph(paragraph.id, detokenize(tokenize(paragraph)))
    candidate = builder.build()

    result = verify(
        VerificationInputs(
            base=document, candidate=candidate, delta=compute_delta(document, candidate)
        )
    )

    assert any(warning.code is WarningCode.REFERENCE_LEFT_UNCITED for warning in result.warnings)
    assert candidate.references == document.references
    assert result.can_accept


def test_structural_damage_blocks(document: Document) -> None:
    broken = document.model_copy(update={"sections": document.sections[:-1]})

    result = verify(
        VerificationInputs(base=document, candidate=broken, delta=compute_delta(document, broken))
    )

    assert not result.can_accept
    assert any(blocker.code is BlockerCode.STRUCTURE_DAMAGED for blocker in result.blockers)


def test_a_citation_pointing_at_a_missing_reference_blocks(document: Document) -> None:
    paragraph = cited_paragraph(document)
    citation_id = paragraph.citation_ids[0]
    node = document.citations[citation_id]
    broken_node = node.model_copy(update={"items": (CitationItem(reference_id="ref_nonexistent"),)})
    broken = document.model_copy(
        update={"citations": {**document.citations, citation_id: broken_node}}
    )

    result = verify(
        VerificationInputs(base=document, candidate=broken, delta=compute_delta(document, broken))
    )

    assert not result.can_accept


def _added(verdict: SupportVerdict, complete: bool = True) -> object:
    csl = CSLItem(
        id="ref_added_001",
        title="A Real Paper" if complete else None,
        author=[{"literal": "A Author"}] if complete else [],
        issued={"date-parts": [[2020]]} if complete else None,
    )
    return AddedCitation(
        paragraph_id="p_1_1",
        sentence_id="p_1_1:s1",
        citation_id="cite_added_001",
        reference=ReferenceRecord(id="ref_added_001", csl=csl),
        source_record_id="src_001",
        verdict=verdict,
        rationale="",
    )


def test_a_partially_supported_addition_warns(document: Document) -> None:
    result = verify(
        VerificationInputs(
            base=document,
            candidate=document,
            delta=compute_delta(document, document),
            added=(_added(SupportVerdict.PARTIALLY_SUPPORTED),),
        )
    )

    assert any(
        warning.code is WarningCode.NEW_CITATION_PARTIAL_SUPPORT for warning in result.warnings
    )
    assert result.can_accept


def test_an_unsupported_addition_blocks(document: Document) -> None:
    result = verify(
        VerificationInputs(
            base=document,
            candidate=document,
            delta=compute_delta(document, document),
            added=(_added(SupportVerdict.CONTRADICTED),),
        )
    )

    assert not result.can_accept
    assert any(blocker.code is BlockerCode.NEW_CITATION_UNSUPPORTED for blocker in result.blockers)


def test_a_reference_whose_csl_id_disagrees_cannot_exist_at_all() -> None:
    with pytest.raises(IdentityInvariantError):
        ReferenceRecord(
            id="ref_added_009",
            csl=CSLItem(
                id="something_else",
                title="A Paper",
                author=[{"literal": "A Author"}],
                issued={"date-parts": [[2020]]},
            ),
        )


def test_an_incomplete_new_reference_blocks(document: Document) -> None:
    bare = ReferenceRecord(id="ref_added_009", csl=CSLItem(id="ref_added_009", title="A Paper"))
    candidate = document.model_copy(update={"references": (*document.references, bare)})

    result = verify(
        VerificationInputs(
            base=document, candidate=candidate, delta=compute_delta(document, candidate)
        )
    )

    assert not result.can_accept
    assert any(blocker.code is BlockerCode.NEW_REFERENCE_INCOMPLETE for blocker in result.blockers)


def test_warning_ids_are_bound_to_the_candidate(document: Document) -> None:
    paragraph = cited_paragraph(document)

    def removal_ids(extra: str) -> set[str]:
        builder = CandidateRevisionBuilder(document)
        builder.replace_paragraph(paragraph.id, detokenize(tokenize(paragraph)) + extra)
        candidate = builder.build()
        result = verify(
            VerificationInputs(
                base=document, candidate=candidate, delta=compute_delta(document, candidate)
            )
        )
        return set(result.required_warning_ids)

    assert removal_ids("") != removal_ids(" An extra sentence.")


def test_a_degraded_provider_is_always_reported(document: Document) -> None:
    result = verify(
        VerificationInputs(
            base=document,
            candidate=document,
            delta=compute_delta(document, document),
            degradations=(
                ProviderDegradation(provider="SEMANTIC_SCHOLAR", code="PROVIDER_RATE_LIMITED"),
            ),
        )
    )

    assert any(warning.code is WarningCode.PROVIDER_DEGRADED for warning in result.warnings)


def test_falling_short_of_the_target_is_reported(document: Document) -> None:
    result = verify(
        VerificationInputs(
            base=document,
            candidate=document,
            delta=compute_delta(document, document),
            achieved_fraction=0.1,
        )
    )

    assert any(warning.code is WarningCode.TARGET_NOT_MET for warning in result.warnings)


def test_an_unchanged_document_needs_no_acknowledgement(document: Document) -> None:
    result = verify(
        VerificationInputs(
            base=document, candidate=document, delta=compute_delta(document, document)
        )
    )

    assert result.required_warning_ids == ()
    assert result.can_accept
