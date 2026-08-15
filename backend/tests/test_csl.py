from __future__ import annotations

from lxml import etree

from app.domain.reference import (
    RAW_TEXT_KEY,
    NormalizationStatus,
    csl_for_unparseable,
)
from app.services.citations.csl import to_csl
from app.services.parser.xml_safety import parse_tei
from tests.conftest import CORPUS, mapped

TEI_OPEN = '<biblStruct xmlns="http://www.tei-c.org/ns/1.0" xml:id="b0">'


def bibl(inner: str) -> etree._Element:
    return parse_tei(f"{TEI_OPEN}{inner}</biblStruct>")


def test_journal_article_maps_to_article_journal() -> None:
    item, status = to_csl(
        bibl(
            "<analytic><title level='a'>Attention is all you need</title>"
            "<author><persName><forename type='first'>Ashish</forename>"
            "<surname>Vaswani</surname></persName></author></analytic>"
            "<monogr><title level='j'>NeurIPS</title>"
            "<imprint><date type='published' when='2017'/>"
            "<biblScope unit='volume'>30</biblScope>"
            "<biblScope unit='page' from='5998' to='6008'/></imprint></monogr>"
        ),
        "ref_imported_001",
    )
    assert item.type == "article-journal"
    assert item.title == "Attention is all you need"
    assert item.container_title == "NeurIPS"
    assert item.author == [{"family": "Vaswani", "given": "Ashish"}]
    assert item.year == 2017
    assert item.model_dump()["volume"] == "30"
    assert item.model_dump()["page"] == "5998-6008"
    assert status is NormalizationStatus.COMPLETE


def test_corporate_author_becomes_a_literal_name() -> None:
    item, _ = to_csl(
        bibl(
            "<monogr><title level='m'>World report</title>"
            "<author><orgName>World Health Organization</orgName></author>"
            "<imprint><date when='2021'/></imprint></monogr>"
        ),
        "ref_imported_002",
    )
    assert item.author == [{"literal": "World Health Organization"}]


def test_unparseable_entry_is_still_a_valid_csl_item() -> None:
    item, status = to_csl(
        bibl("<note type='raw_reference'>Some garbled string, 19xx</note>"),
        "ref_imported_003",
    )
    assert item.id == "ref_imported_003"
    assert item.type == "document"
    assert status is NormalizationStatus.RAW_ONLY
    assert item.raw_text == "Some garbled string, 19xx"


def test_raw_text_lives_in_custom_never_in_note() -> None:
    item = csl_for_unparseable("ref_imported_004", "unparseable")
    dumped = item.model_dump()
    assert dumped["custom"][RAW_TEXT_KEY] == "unparseable"
    assert "note" not in dumped


def test_doi_is_stored_bare() -> None:
    item, _ = to_csl(
        bibl(
            "<analytic><title level='a'>A paper</title></analytic>"
            "<monogr><idno type='DOI'>https://doi.org/10.1000/xyz</idno>"
            "<imprint><date when='2020'/></imprint></monogr>"
        ),
        "ref_imported_005",
    )
    assert item.DOI == "10.1000/xyz"


def test_arxiv_id_is_kept_out_of_the_rendered_fields() -> None:
    item, _ = to_csl(
        bibl(
            "<monogr><title level='m'>Layer normalization</title>"
            "<idno type='arXiv'>arXiv:1607.06450v1</idno>"
            "<imprint><date when='2016'/></imprint></monogr>"
        ),
        "ref_imported_006",
    )
    assert item.custom["arxiv_id"] == "1607.06450v1"
    assert item.model_dump().get("note") is None


def test_insertion_completeness_is_stricter_than_preservation() -> None:
    incomplete, _ = to_csl(
        bibl("<analytic><title level='a'>Title only</title></analytic>"),
        "ref_imported_007",
    )
    assert incomplete.is_complete_for_insertion() is False


def test_corpus_references_are_mostly_complete() -> None:
    for name in CORPUS:
        references = mapped(name).document.references
        complete = sum(
            1 for r in references if r.normalization_status is NormalizationStatus.COMPLETE
        )
        assert complete > len(references) * 0.8
