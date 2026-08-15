"""Postvalidation: the independent check, and what it is allowed to conclude.

The property that gives these numbers meaning is that the expectation and the
observation come from different places. ``bibliography_order`` is ``listBibl``
position; the printed number is the marker string. A test suite that only checked
"the validator agreed with the mapper" would pass on a validator that returns
True.
"""

from __future__ import annotations

import pytest

from app.domain.citation import SemanticParseStatus
from app.services.parser.postvalidator import LinkageCode, postvalidate
from app.services.parser.tei_mapper import CitationFamily
from tests.conftest import mapped, validated


def test_numeric_linkage_agrees_across_the_whole_corpus() -> None:
    """On both numeric papers, every checkable marker number matches its entry.

    155 and 58 independent agreements is the strongest single statement this
    pipeline can make about extraction quality, and it is measured, not claimed.
    """
    for name in ("A_numeric", "C_numeric_dense"):
        report = validated(name).report
        assert report.family is CitationFamily.NUMERIC
        assert report.checked > 50
        assert report.mismatched == 0
        assert report.linkage_accuracy == 1.0


def test_postvalidation_recovers_links_grobid_missed() -> None:
    """GROBID leaves author-year markers untargeted; surname and year link them.

    This is postvalidation earning its place: it converts silently unlinked
    citations into real ones, and promotes the clusters that had been downgraded
    because a single item in them failed to resolve.
    """
    before = mapped("B_author_year")
    after = postvalidate(before)

    assert after.report.recovered > 0
    assert after.report.promoted_citation_ids

    raw_before = sum(
        1
        for node in before.document.citations.values()
        if node.semantic_parse_status is SemanticParseStatus.RAW_ONLY
    )
    raw_after = sum(
        1
        for node in after.document.citations.values()
        if node.semantic_parse_status is SemanticParseStatus.RAW_ONLY
    )
    assert raw_after < raw_before


def test_promotion_only_happens_when_every_item_is_linked() -> None:
    for node in validated("B_author_year").document.citations.values():
        if node.semantic_parse_status is SemanticParseStatus.STRUCTURED:
            assert all(item.is_linked for item in node.items)


def test_ambiguous_author_year_is_left_unlinked() -> None:
    """Two works by one author in one year cannot be told apart.

    The marker's disambiguating letter ("2018a") is not in the bibliography's
    structured fields, so linking either one would be a coin flip presented as a
    fact.
    """
    report = validated("B_author_year").report
    assert report.ambiguous > 0
    for issue in report.issues:
        if issue.code is LinkageCode.AMBIGUOUS:
            assert issue.linked_reference_id is None
            assert issue.expected_reference_id is None


def test_grobid_false_positives_are_refused_not_linked() -> None:
    """A table row reading "16 32 64" is not citation 16.

    Without the marker-shape guard, the recovery path would give this a
    confident link to whatever sits at bibliography position 16.
    """
    report = validated("C_numeric_dense").report
    false_positives = [i for i in report.issues if i.code is LinkageCode.NOT_A_MARKER]
    assert false_positives
    for issue in false_positives:
        assert issue.linked_reference_id is None
        assert issue.expected_reference_id is None


def test_weak_disagreement_reports_but_does_not_destroy() -> None:
    """One agreeing signal against one disagreeing signal is uncertainty.

    GROBID splits "Rie Kubota Ando" into family "Kubota", and dates the 2013
    word2vec paper 2009. Downgrading on either alone would destroy correct
    citations to punish a misparsed field, so both are reported as UNCERTAIN and
    the link is kept.
    """
    result = validated("B_author_year")
    uncertain = [i for i in result.report.issues if i.code is LinkageCode.UNCERTAIN]
    assert uncertain
    for issue in uncertain:
        assert issue.linked_reference_id is not None
        node = result.document.citations[issue.citation_id]
        assert issue.citation_id not in result.report.downgraded_citation_ids
        assert node.items[issue.item_index].is_linked


def test_mismatch_requires_both_signals_to_disagree() -> None:
    for issue in validated("B_author_year").report.issues:
        if issue.code is LinkageCode.MISMATCH:
            assert "Neither signal agrees" in issue.detail or "no record" in issue.detail


def test_downgraded_occurrences_become_raw_only() -> None:
    result = validated("B_author_year")
    for identifier in result.report.downgraded_citation_ids:
        node = result.document.citations[identifier]
        assert node.semantic_parse_status is SemanticParseStatus.RAW_ONLY
        assert node.fidelity_exportable is False


def test_bibliography_is_never_lost(corpus_name: str) -> None:
    """Every ``listBibl`` entry survives to a reference record.

    Bibliography preservation is the one thing a researcher would notice
    immediately and never forgive.
    """
    report = validated(corpus_name).report
    assert report.bibliography_expected > 0
    assert report.bibliography_retained == report.bibliography_expected
    assert report.bibliography_complete


def test_accuracy_denominator_counts_only_checkable_items(corpus_name: str) -> None:
    """``checked`` excludes items with no independent signal.

    Counting unverifiable items as successes would inflate the number the
    evaluation reports, which is exactly the dishonesty this project is about.
    """
    report = validated(corpus_name).report
    assert report.checked == report.agreed + report.mismatched
    assert report.checked <= len(report.issues)


def test_postvalidation_is_idempotent(corpus_name: str) -> None:
    once = validated(corpus_name)
    twice = postvalidate(mapped(corpus_name))
    assert once.document.content_hash() == twice.document.content_hash()


@pytest.mark.parametrize("name", ["A_numeric", "C_numeric_dense"])
def test_numeric_recovery_uses_the_printed_number(name: str) -> None:
    """Recovery, when it fires, must be justified by the marker itself."""
    for issue in validated(name).report.issues:
        if issue.code is LinkageCode.RECOVERED:
            assert issue.expected_reference_id is not None
            assert any(char.isdigit() for char in issue.marker_fragment)
