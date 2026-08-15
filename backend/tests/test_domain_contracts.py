from __future__ import annotations

import pytest

from app.domain.citation import (
    CitationItem,
    CitationMode,
    CitationNode,
    LocatorLabel,
    SemanticParseStatus,
)
from app.domain.delta import ComputedEditDelta
from app.domain.document import CitationRef, Document, Paragraph, Section, TextRun
from app.domain.errors import IdentityInvariantError
from app.domain.hashing import canonical_sha256, normalize_text, text_sha256
from app.domain.ids import Origin, origin_of, parse_sentence_id, sentence_id
from app.domain.proposal import CandidateRevisionSnapshot
from app.domain.reference import CSLItem, ReferenceRecord, csl_for_unparseable
from app.domain.verification import (
    VerificationResult,
    VerificationWarning,
    WarningCode,
)


def test_canonical_hash_ignores_key_order() -> None:
    assert canonical_sha256({"a": 1, "b": 2}) == canonical_sha256({"b": 2, "a": 1})


def test_canonical_hash_normalises_unicode() -> None:
    composed = "café"
    decomposed = "cafe\u0301"
    assert composed != decomposed
    assert canonical_sha256(composed) == canonical_sha256(decomposed)


def test_text_hash_ignores_reflow_but_not_rewording() -> None:
    original = "The model was trained for 100 epochs."
    reflowed = "The model was trained\n  for 100 epochs."
    reworded = "The model was trained for 200 epochs."
    assert text_sha256(original) == text_sha256(reflowed)
    assert text_sha256(original) != text_sha256(reworded)


def test_normalize_text_collapses_whitespace() -> None:
    assert normalize_text("  a \n\t b  ") == "a b"


def test_reference_rejects_mismatched_csl_id() -> None:
    with pytest.raises(IdentityInvariantError):
        ReferenceRecord(id="ref_imported_001", csl=CSLItem(id="something_else"))


def test_reference_accepts_matching_csl_id() -> None:
    record = ReferenceRecord(id="ref_imported_001", csl=CSLItem(id="ref_imported_001"))
    assert record.origin is Origin.IMPORTED


def test_unparseable_reference_still_has_valid_csl() -> None:
    item = csl_for_unparseable("ref_imported_009", "Smith, J. Some unparseable string.")
    record = ReferenceRecord(id="ref_imported_009", csl=item, raw_text=item.raw_text)
    assert record.csl.type == "document"
    assert record.csl.raw_text.startswith("Smith")

    assert "note" not in record.csl.model_dump(exclude_none=True)


def test_origin_is_readable_from_the_identifier() -> None:
    assert origin_of("cite_imported_012") is Origin.IMPORTED
    assert origin_of("ref_added_003") is Origin.ADDED
    assert origin_of("p_1_2") is None


def test_sentence_ids_round_trip() -> None:
    value = sentence_id("p_1_2", 3)
    assert parse_sentence_id(value) == ("p_1_2", 3)
    assert parse_sentence_id("not-a-sentence-id") is None


def test_partial_modifiers_is_not_fidelity_exportable() -> None:
    node = CitationNode(
        id="cite_imported_012",
        items=(CitationItem(reference_id="ref_imported_001"),),
        raw_marker="[1, p. 4]",
        semantic_parse_status=SemanticParseStatus.PARTIAL_MODIFIERS,
    )
    assert node.fidelity_exportable is False


def test_structured_citation_is_fidelity_exportable() -> None:
    node = CitationNode(
        id="cite_imported_012",
        items=(CitationItem(reference_id="ref_imported_001"),),
        raw_marker="[1]",
    )
    assert node.fidelity_exportable is True


def test_locator_encodes_into_the_suffix() -> None:
    item = CitationItem(
        reference_id="ref_imported_002", locator="14", locator_label=LocatorLabel.PAGE
    )
    assert item.encoded_suffix() == ", p. 14"


def test_signature_distinguishes_mode() -> None:
    normal = CitationItem(reference_id="ref_imported_001", mode=CitationMode.NORMAL)
    narrative = CitationItem(reference_id="ref_imported_001", mode=CitationMode.AUTHOR_IN_TEXT)
    assert normal.signature() != narrative.signature()


def _document() -> Document:
    paragraph = Paragraph(
        id="p_1_1",
        order_index=0,
        inlines=(
            TextRun(text="Transformers changed sequence modelling "),
            CitationRef(citation_id="cite_imported_001"),
            TextRun(text="."),
        ),
    )
    return Document(
        title="A paper",
        sections=(Section(id="sec_1", title="Introduction", body=(paragraph,)),),
        citations={
            "cite_imported_001": CitationNode(
                id="cite_imported_001",
                items=(CitationItem(reference_id="ref_imported_001"),),
                raw_marker="[1]",
            )
        },
        references=(
            ReferenceRecord(id="ref_imported_001", csl=CSLItem(id="ref_imported_001")),
            ReferenceRecord(id="ref_imported_002", csl=CSLItem(id="ref_imported_002")),
        ),
    )


def test_paragraph_text_excludes_citation_markers() -> None:
    document = _document()
    paragraph = document.paragraph("p_1_1")
    assert paragraph is not None
    assert paragraph.text == "Transformers changed sequence modelling."
    assert paragraph.word_count() == 4
    assert paragraph.citation_ids == ("cite_imported_001",)


def test_cited_reference_ids_exclude_uncited_works() -> None:
    document = _document()
    assert document.cited_reference_ids() == {"ref_imported_001"}
    uncited = {r.id for r in document.references} - document.cited_reference_ids()
    assert uncited == {"ref_imported_002"}


def test_warning_id_is_bound_to_content() -> None:
    first = VerificationWarning.build(
        WarningCode.CITATION_REMOVED,
        "Citation [12] will be removed from the Introduction.",
        ("cite_imported_012",),
        content_hash="candidate-a",
    )
    same = VerificationWarning.build(
        WarningCode.CITATION_REMOVED,
        "Citation [12] will be removed from the Introduction.",
        ("cite_imported_012",),
        content_hash="candidate-a",
    )
    regenerated = VerificationWarning.build(
        WarningCode.CITATION_REMOVED,
        "Citation [12] will be removed from the Introduction.",
        ("cite_imported_012",),
        content_hash="candidate-b",
    )
    other_subject = VerificationWarning.build(
        WarningCode.CITATION_REMOVED,
        "Citation [7] will be removed from the Introduction.",
        ("cite_imported_007",),
        content_hash="candidate-a",
    )

    assert first.id == same.id
    assert first.id != regenerated.id
    assert first.id != other_subject.id


def test_required_warning_ids_are_sorted_and_complete() -> None:
    warnings = tuple(
        VerificationWarning.build(WarningCode.CITATION_REMOVED, "m", (subject,), "c")
        for subject in ("cite_imported_003", "cite_imported_001")
    )
    result = VerificationResult(warnings=warnings)
    assert result.required_warning_ids == tuple(sorted(w.id for w in warnings))
    assert result.can_accept is True


def test_snapshot_hash_covers_verification_not_just_prose() -> None:
    document = _document()
    delta = ComputedEditDelta()
    clean = CandidateRevisionSnapshot(
        base_revision_id="rev_1",
        document=document,
        delta=delta,
        verification=VerificationResult(),
    )
    warned = CandidateRevisionSnapshot(
        base_revision_id="rev_1",
        document=document,
        delta=delta,
        verification=VerificationResult(
            warnings=(
                VerificationWarning.build(
                    WarningCode.CITATION_REMOVED, "m", ("cite_imported_012",), "c"
                ),
            )
        ),
    )
    assert clean.snapshot_sha256 != warned.snapshot_sha256


def test_snapshot_hash_is_stable_across_construction() -> None:
    document = _document()
    first = CandidateRevisionSnapshot(
        base_revision_id="rev_1",
        document=document,
        delta=ComputedEditDelta(),
        verification=VerificationResult(),
    )
    second = CandidateRevisionSnapshot(
        base_revision_id="rev_1",
        document=_document(),
        delta=ComputedEditDelta(),
        verification=VerificationResult(),
    )
    assert first.snapshot_sha256 == second.snapshot_sha256
