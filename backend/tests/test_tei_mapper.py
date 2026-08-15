from __future__ import annotations

from app.domain.block import BlockKind, FidelityStatus
from app.domain.citation import CitationMode, SemanticParseStatus
from app.domain.document import CitationRef, Paragraph
from app.domain.reference import NormalizationStatus
from app.services.parser.tei_mapper import CitationFamily
from tests.conftest import mapped


def test_families_are_detected_from_markers() -> None:
    assert mapped("A_numeric").diagnostics.family is CitationFamily.NUMERIC
    assert mapped("C_numeric_dense").diagnostics.family is CitationFamily.NUMERIC
    assert mapped("B_author_year").diagnostics.family is CitationFamily.AUTHOR_YEAR


def test_adjacent_refs_form_one_occurrence() -> None:
    document = mapped("A_numeric").document
    clusters = [node for node in document.citations.values() if len(node.items) == 3]
    assert clusters, "the corpus contains three-work clusters"
    assert any(node.raw_marker == "[35, 2, 5]" for node in document.citations.values())

    node = next(n for n in document.citations.values() if n.raw_marker == "[35, 2, 5]")
    assert len(node.items) == 3
    assert all(item.is_linked for item in node.items)
    assert node.semantic_parse_status is SemanticParseStatus.STRUCTURED


def test_opening_bracket_is_absorbed_from_preceding_prose() -> None:
    result = mapped("B_author_year")
    assert result.diagnostics.absorbed_openers > 0

    marker = next(
        node.raw_marker
        for node in result.document.citations.values()
        if node.raw_marker.startswith("(Dai and Le")
    )
    assert marker.startswith("(")
    assert marker.endswith(")")
    assert "Dai and Le, 2015" in marker

    paragraph = next(
        p
        for p in result.document.paragraphs()
        if any(
            isinstance(node, CitationRef)
            and result.document.citations[node.citation_id].raw_marker.startswith("(Dai and Le")
            for node in p.inlines
        )
    )
    assert "(Dai and" not in paragraph.text


def test_absorption_refuses_to_eat_prose() -> None:
    for name in ("A_numeric", "B_author_year", "C_numeric_dense"):
        for node in mapped(name).document.citations.values():
            assert len(node.raw_marker) < 300


def test_author_in_text_mode_is_recognised() -> None:
    document = mapped("B_author_year").document
    narrative = [
        node
        for node in document.citations.values()
        if any(item.mode is CitationMode.AUTHOR_IN_TEXT for item in node.items)
    ]
    assert narrative, "the corpus uses narrative citations"
    for node in narrative:
        assert not node.raw_marker.startswith("(")
        assert node.raw_marker.rstrip(".,;").endswith(")")


def test_unlinked_marker_is_raw_only_not_silently_dropped() -> None:
    document = mapped("B_author_year").document
    unlinked = [node for node in document.citations.values() if node.is_unlinked]
    assert unlinked
    for node in unlinked:
        assert node.semantic_parse_status is SemanticParseStatus.RAW_ONLY
        assert node.fidelity_exportable is False
        assert node.raw_marker


def test_partially_linked_cluster_is_raw_only() -> None:
    document = mapped("B_author_year").document
    for node in document.citations.values():
        linked = sum(1 for item in node.items if item.is_linked)
        if 0 < linked < len(node.items):
            assert node.semantic_parse_status is SemanticParseStatus.RAW_ONLY


def test_reference_ids_match_their_csl_ids(corpus_name: str) -> None:
    for reference in mapped(corpus_name).document.references:
        assert reference.csl.id == reference.id


def test_bibliography_order_is_positional(corpus_name: str) -> None:
    references = mapped(corpus_name).document.references
    assert [r.bibliography_order for r in references] == list(range(1, len(references) + 1))


def test_every_reference_has_valid_csl(corpus_name: str) -> None:
    for reference in mapped(corpus_name).document.references:
        assert reference.csl.type
        if reference.normalization_status is NormalizationStatus.RAW_ONLY:
            assert reference.csl.type == "document"
            assert reference.csl.raw_text or reference.raw_text


def test_raw_text_never_lands_in_the_note_field(corpus_name: str) -> None:
    for reference in mapped(corpus_name).document.references:
        extra = reference.csl.model_dump()
        assert not extra.get("note")


def test_figures_declaring_a_graphic_are_unrenderable() -> None:
    document = mapped("A_numeric").document
    graphics = [
        block
        for block in document.blocks.values()
        if block.kind is BlockKind.FIGURE and block.missing_asset
    ]
    assert graphics
    assert all(block.source_boxes for block in graphics)
    for block in graphics:
        assert block.fidelity_status is FidelityStatus.UNRENDERABLE
        assert block.id in document.unrenderable_block_ids()


def test_tables_are_converted_with_content(corpus_name: str) -> None:
    for block in mapped(corpus_name).document.blocks.values():
        if block.kind is BlockKind.TABLE and block.normalized_content:
            assert block.fidelity_status is FidelityStatus.CONVERTED
            assert "|" in block.normalized_content


def test_every_block_keeps_its_raw_source(corpus_name: str) -> None:
    for block in mapped(corpus_name).document.blocks.values():
        assert block.raw_source.strip()


def test_floating_blocks_are_recorded_not_guessed_into_the_flow() -> None:
    result = mapped("C_numeric_dense")
    document = result.document
    assert document.floating_block_ids
    referenced = {
        node.block_id
        for section in document.sections
        for node in section.body
        if not isinstance(node, Paragraph)
    }
    assert not referenced & set(document.floating_block_ids)


def test_back_matter_prose_is_mapped(corpus_name: str) -> None:
    result = mapped(corpus_name)
    assert result.diagnostics.out_of_scope_refs <= 5, (
        "citations outside mapped prose should be a handful of captions and "
        "footnotes, not a whole appendix"
    )


def test_no_range_expansion_without_confirmed_numeric_family() -> None:
    for name in ("A_numeric", "B_author_year", "C_numeric_dense"):
        assert mapped(name).diagnostics.ranges_expanded == 0


def test_mapping_is_deterministic(corpus_name: str) -> None:
    assert mapped(corpus_name).document.content_hash() == (
        mapped(corpus_name).document.content_hash()
    )
