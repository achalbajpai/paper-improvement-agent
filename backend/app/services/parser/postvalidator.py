"""Independent verification of what the mapper linked.

The mapper links a marker to a reference through GROBID's XML-ID. This module
checks that link against a *different* signal -- the number or the author-year the
reader actually saw printed, compared with a bibliography order derived from
``listBibl`` position. Neither quantity is computed from the other, which is the
only reason the check means anything. A validator that re-derives its expectation
from the string it is checking always passes, and the evaluation that reports its
pass rate measures nothing.

It does three things, in order of value:

1. **Recovers** links GROBID missed. On the corpus, GROBID leaves author-year
   markers such as ``(Peters et al., 2018a)`` untargeted; matching surname and
   year against the bibliography links them, and promotes whole clusters that had
   been downgraded because one of four items failed to resolve.
2. **Downgrades** links the evidence contradicts, to ``RAW_ONLY``. A citation
   pointing at the wrong paper is worse than one that admits it is only text.
3. **Reports** what it could not settle, so parse quality shows uncertainty
   instead of hiding it.

Numeric and author-year evidence are not treated as equally strong, because they
are not. A printed ``[13]`` disagreeing with bibliography position 13 is decisive.
A surname disagreeing is not: GROBID mis-splits names (``Rie Kubota Ando`` becomes
family ``Kubota``), so a surname mismatch is at least as likely to be a metadata
error as a linkage error, and downgrading on it would destroy good citations.

Pure: no I/O.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from app.domain.citation import CitationNode, SemanticParseStatus
from app.domain.document import Document
from app.domain.reference import ReferenceRecord
from app.services.parser.tei_mapper import (
    CitationFamily,
    LinkageEvidence,
    MappedDocument,
)

_NUMERIC_FRAGMENT = re.compile(r"^[\[\(]?\s*(\d{1,3})\s*[\]\),;\.]*$")

_AUTHOR_YEAR_FRAGMENT = re.compile(
    r"(?P<surname>[A-Z][\w'\u2019-]+)"
    r"(?:\s+(?:and|&)\s+[A-Z][\w'\u2019-]+)?"
    r"(?:\s+et\s+al\.?)?"
    r"[,\s]+(?:\(\s*)?(?P<year>1[6-9]\d{2}|20\d{2})",
    re.UNICODE,
)


class LinkageCode(StrEnum):
    AGREED = "AGREED"

    MISMATCH = "MISMATCH"

    RECOVERED = "RECOVERED"

    AMBIGUOUS = "AMBIGUOUS"

    UNRESOLVED = "UNRESOLVED"

    UNCERTAIN = "UNCERTAIN"

    NOT_A_MARKER = "NOT_A_MARKER"


@dataclass(frozen=True)
class LinkageIssue:
    citation_id: str
    item_index: int
    code: LinkageCode
    marker_fragment: str
    linked_reference_id: str | None
    expected_reference_id: str | None
    detail: str = ""


@dataclass(frozen=True)
class PostValidationReport:
    family: CitationFamily

    checked: int
    agreed: int
    mismatched: int
    recovered: int
    ambiguous: int
    unresolved: int
    uncertain: int
    not_a_marker: int
    downgraded_citation_ids: tuple[str, ...]
    promoted_citation_ids: tuple[str, ...]
    bibliography_expected: int
    bibliography_retained: int
    issues: tuple[LinkageIssue, ...]

    @property
    def bibliography_complete(self) -> bool:
        return self.bibliography_expected == self.bibliography_retained

    @property
    def linkage_accuracy(self) -> float:
        """Agreement rate among items where a check was possible."""
        return self.agreed / self.checked if self.checked else 0.0


@dataclass(frozen=True)
class ValidatedDocument:
    document: Document
    report: PostValidationReport


def postvalidate(mapped: MappedDocument) -> ValidatedDocument:
    document = mapped.document
    family = mapped.diagnostics.family
    index = _ReferenceIndex(document.references)

    issues: list[LinkageIssue] = []

    repairs: dict[str, dict[int, str]] = defaultdict(dict)

    for evidence in mapped.diagnostics.linkage:
        issue, recovered = _check(evidence, family, index, document)
        issues.append(issue)
        if recovered is not None:
            repairs[evidence.citation_id][evidence.item_index] = recovered

    downgrade = {issue.citation_id for issue in issues if issue.code is LinkageCode.MISMATCH}
    citations, promoted = _apply(document, repairs, downgrade)

    counts = _counts(issues)
    report = PostValidationReport(
        family=family,
        checked=counts[LinkageCode.AGREED] + counts[LinkageCode.MISMATCH],
        agreed=counts[LinkageCode.AGREED],
        mismatched=counts[LinkageCode.MISMATCH],
        recovered=counts[LinkageCode.RECOVERED],
        ambiguous=counts[LinkageCode.AMBIGUOUS],
        unresolved=counts[LinkageCode.UNRESOLVED],
        uncertain=counts[LinkageCode.UNCERTAIN],
        not_a_marker=counts[LinkageCode.NOT_A_MARKER],
        downgraded_citation_ids=tuple(sorted(downgrade)),
        promoted_citation_ids=promoted,
        bibliography_expected=mapped.diagnostics.tei_bibl_entries,
        bibliography_retained=len(document.references),
        issues=tuple(issues),
    )
    return ValidatedDocument(
        document=document.model_copy(update={"citations": citations}), report=report
    )


def _check(
    evidence: LinkageEvidence,
    family: CitationFamily,
    index: _ReferenceIndex,
    document: Document,
) -> tuple[LinkageIssue, str | None]:
    """Return the finding for one item, and a reference id to link if recovered."""
    if family is CitationFamily.NUMERIC:
        return _check_numeric(evidence, index)
    if family is CitationFamily.AUTHOR_YEAR:
        return _check_author_year(evidence, index, document)
    return (
        _issue(evidence, LinkageCode.UNCERTAIN, detail="Citation family could not be determined."),
        None,
    )


def _check_numeric(
    evidence: LinkageEvidence, index: _ReferenceIndex
) -> tuple[LinkageIssue, str | None]:
    match = _NUMERIC_FRAGMENT.match(evidence.marker_fragment.strip())
    printed = int(match.group(1)) if match else None

    if evidence.reference_id is None:
        if printed is None:
            return _issue(evidence, LinkageCode.NOT_A_MARKER), None
        candidate = index.by_order.get(printed)
        if candidate is None:
            return _issue(evidence, LinkageCode.UNRESOLVED), None
        return (
            _issue(
                evidence,
                LinkageCode.RECOVERED,
                expected=candidate.id,
                detail=f"Printed [{printed}] matches bibliography position {printed}.",
            ),
            candidate.id,
        )

    if printed is None:
        return (
            _issue(
                evidence,
                LinkageCode.UNCERTAIN,
                detail="Marker text carries no number to check the link against.",
            ),
            None,
        )
    expected = index.by_order.get(printed)
    if expected is not None and expected.id == evidence.reference_id:
        return _issue(evidence, LinkageCode.AGREED, expected=expected.id), None
    return (
        _issue(
            evidence,
            LinkageCode.MISMATCH,
            expected=expected.id if expected else None,
            detail=(
                f"Marker prints [{printed}] but the link resolves to bibliography "
                f"position {evidence.bibliography_order}."
            ),
        ),
        None,
    )


def _check_author_year(
    evidence: LinkageEvidence, index: _ReferenceIndex, document: Document
) -> tuple[LinkageIssue, str | None]:
    match = _AUTHOR_YEAR_FRAGMENT.search(evidence.marker_fragment)
    if match is None:
        if evidence.reference_id is None:
            return _issue(evidence, LinkageCode.NOT_A_MARKER), None
        return (
            _issue(
                evidence,
                LinkageCode.UNCERTAIN,
                detail="Marker text carries no author-year pair to check the link against.",
            ),
            None,
        )

    surname = _fold(match.group("surname"))
    year = int(match.group("year"))

    if evidence.reference_id is None:
        return _recover_author_year(evidence, index, surname=surname, year=year)
    return _verify_author_year(evidence, document, surname=surname, year=year)


def _recover_author_year(
    evidence: LinkageEvidence, index: _ReferenceIndex, *, surname: str, year: int
) -> tuple[LinkageIssue, str | None]:
    candidates = index.by_author_year.get((surname, year), [])
    if len(candidates) == 1:
        return (
            _issue(
                evidence,
                LinkageCode.RECOVERED,
                expected=candidates[0].id,
                detail=f"'{surname} {year}' matches exactly one bibliography entry.",
            ),
            candidates[0].id,
        )
    if len(candidates) > 1:
        return (
            _issue(
                evidence,
                LinkageCode.AMBIGUOUS,
                detail=f"'{surname} {year}' matches {len(candidates)} bibliography entries.",
            ),
            None,
        )
    return _issue(evidence, LinkageCode.UNRESOLVED), None


def _verify_author_year(
    evidence: LinkageEvidence, document: Document, *, surname: str, year: int
) -> tuple[LinkageIssue, str | None]:
    """Corroborate an author-year link against the marker the reader saw.

    Two independent signals are available, and both are noisy in a *known*
    direction. GROBID splits ``Rie Kubota Ando`` into family ``Kubota``, so a
    surname disagreement is often a metadata error. It also misdates entries --
    on the corpus it dates the 2013 ``word2vec`` paper 2009 -- so a year
    disagreement is often a metadata error too.

    A link is therefore downgraded only when **neither** signal corroborates it.
    One agreeing signal against one disagreeing signal is reported as uncertain
    and left alone, because the alternative is destroying correct citations to
    punish a misparsed field.
    """
    record = document.reference(evidence.reference_id or "")
    if record is None:
        return _issue(evidence, LinkageCode.MISMATCH, detail="Link points at no record."), None

    names = _surnames(record)
    surname_agrees = surname in names if names else None
    year_agrees = record.csl.year == year if record.csl.year is not None else None

    if surname_agrees and year_agrees:
        return _issue(evidence, LinkageCode.AGREED, expected=evidence.reference_id), None
    if surname_agrees is False and year_agrees is False:
        return (
            _issue(
                evidence,
                LinkageCode.MISMATCH,
                detail=(
                    f"Marker prints '{surname} {year}'; the linked entry is "
                    f"{sorted(names)} {record.csl.year}. Neither signal agrees."
                ),
            ),
            None,
        )
    if surname_agrees is False:
        return (
            _issue(
                evidence,
                LinkageCode.UNCERTAIN,
                detail=(
                    f"Marker prints '{surname}'; the linked entry's authors parsed as "
                    f"{sorted(names)}. The year agrees, so the author metadata is "
                    "the likelier error."
                ),
            ),
            None,
        )
    if year_agrees is False:
        return (
            _issue(
                evidence,
                LinkageCode.UNCERTAIN,
                detail=(
                    f"Marker prints {year}; the linked entry parsed as {record.csl.year}. "
                    "The author agrees, so the entry's date is the likelier error."
                ),
            ),
            None,
        )

    if surname_agrees or year_agrees:
        return _issue(evidence, LinkageCode.AGREED, expected=evidence.reference_id), None
    return (
        _issue(
            evidence,
            LinkageCode.UNCERTAIN,
            detail="The linked entry has neither an author nor a date to check against.",
        ),
        None,
    )


def _issue(
    evidence: LinkageEvidence,
    code: LinkageCode,
    *,
    expected: str | None = None,
    detail: str = "",
) -> LinkageIssue:
    return LinkageIssue(
        citation_id=evidence.citation_id,
        item_index=evidence.item_index,
        code=code,
        marker_fragment=evidence.marker_fragment,
        linked_reference_id=evidence.reference_id,
        expected_reference_id=expected,
        detail=detail,
    )


def _counts(issues: list[LinkageIssue]) -> dict[LinkageCode, int]:
    counts = dict.fromkeys(LinkageCode, 0)
    for issue in issues:
        counts[issue.code] += 1
    return counts


def _apply(
    document: Document,
    repairs: dict[str, dict[int, str]],
    downgrade: set[str],
) -> tuple[dict[str, CitationNode], tuple[str, ...]]:
    citations = dict(document.citations)
    promoted: list[str] = []

    for identifier, node in document.citations.items():
        updated = node
        item_repairs = repairs.get(identifier)
        if item_repairs:
            items = list(node.items)
            for position, reference in item_repairs.items():
                if position < len(items):
                    items[position] = items[position].model_copy(update={"reference_id": reference})
            updated = updated.model_copy(update={"items": tuple(items)})

        if identifier in downgrade:
            updated = updated.model_copy(
                update={"semantic_parse_status": SemanticParseStatus.RAW_ONLY}
            )
        elif (
            updated.semantic_parse_status is SemanticParseStatus.RAW_ONLY
            and updated.items
            and all(item.is_linked for item in updated.items)
        ):
            updated = updated.model_copy(
                update={"semantic_parse_status": SemanticParseStatus.STRUCTURED}
            )
            promoted.append(identifier)

        citations[identifier] = updated

    return citations, tuple(promoted)


def _fold(value: str) -> str:
    """Case- and accent-insensitive key. 'Schölkopf' and 'Scholkopf' are one name."""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return stripped.casefold().strip()


def _surnames(record: ReferenceRecord) -> set[str]:
    names: set[str] = set()
    for author in record.csl.author:
        family = author.get("family") or author.get("literal") or ""
        if not family:
            continue
        names.add(_fold(str(family)))

        names.update(_fold(part) for part in str(family).split() if part)
    return names


class _ReferenceIndex:
    """Lookups built only from bibliography position and parsed metadata."""

    def __init__(self, references: tuple[ReferenceRecord, ...]) -> None:
        self.by_order: dict[int, ReferenceRecord] = {
            record.bibliography_order: record for record in references
        }
        self.by_author_year: dict[tuple[str, int], list[ReferenceRecord]] = defaultdict(list)
        for record in references:
            year = record.csl.year
            if year is None:
                continue
            for surname in _surnames(record):
                self.by_author_year[(surname, year)].append(record)


__all__ = [
    "LinkageCode",
    "LinkageIssue",
    "PostValidationReport",
    "ValidatedDocument",
    "postvalidate",
]
