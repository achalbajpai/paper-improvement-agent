from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, "/srv")

from app.domain.block import BlockKind
from app.domain.citation import CitationNode, SemanticParseStatus
from app.domain.document import Document
from app.domain.reference import NormalizationStatus, ReferenceRecord
from app.services.parser.postvalidator import (
    LinkageCode,
    PostValidationReport,
    postvalidate,
)
from app.services.parser.tei_mapper import MappedDocument, map_tei
from app.services.parser.xml_safety import parse_tei

ROOT = Path(__file__).resolve().parent
LABELS = ROOT / "labels"
RESULTS = ROOT / "results"
TEI = Path("/srv/tests/fixtures/tei")


def normalise_marker(marker: str) -> str:
    collapsed = " ".join(marker.split())
    return collapsed.rstrip(".,;").strip().casefold()


def numeric_signature(
    node: CitationNode, references: dict[str, ReferenceRecord]
) -> tuple[int, ...]:
    positions: list[int] = []
    for item in node.items:
        record = references.get(item.reference_id or "")
        positions.append(record.bibliography_order if record else -1)
    return tuple(positions)


@dataclass
class MarkerScore:
    labelled: int = 0
    detected: int = 0
    cluster_correct: int = 0
    linkage_correct: int = 0
    linkage_checkable: int = 0
    mode_correct: int = 0
    missing: list[str] = field(default_factory=list)
    wrong_linkage: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "labelled": self.labelled,
            "detected": self.detected,
            "detection_rate": _ratio(self.detected, self.labelled),
            "cluster_correct": self.cluster_correct,
            "cluster_rate": _ratio(self.cluster_correct, self.detected),
            "linkage_checkable": self.linkage_checkable,
            "linkage_correct": self.linkage_correct,
            "linkage_rate": _ratio(self.linkage_correct, self.linkage_checkable),
            "mode_correct": self.mode_correct,
            "mode_rate": _ratio(self.mode_correct, self.detected),
            "missing_markers": self.missing,
            "wrong_linkage": self.wrong_linkage,
        }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def score_markers(document: Document, labels: dict[str, Any]) -> MarkerScore:
    score = MarkerScore()
    references = {record.id: record for record in document.references}
    family = labels["family"]

    remaining: list[CitationNode] = list(document.citations.values())

    for label in labels["markers"]:
        score.labelled += 1
        target = normalise_marker(label["marker"])
        match = next(
            (node for node in remaining if normalise_marker(node.raw_marker) == target),
            None,
        )
        if match is None:
            score.missing.append(label["marker"])
            continue
        remaining.remove(match)
        score.detected += 1

        if len(match.items) == len(label["works"]):
            score.cluster_correct += 1

        if label["mode"] in {item.mode.value for item in match.items}:
            score.mode_correct += 1

        if family == "NUMERIC":
            score.linkage_checkable += 1
            observed = numeric_signature(match, references)
            expected = tuple(label["works"])
            if observed == expected:
                score.linkage_correct += 1
            else:
                score.wrong_linkage.append(
                    {
                        "marker": label["marker"],
                        "expected": list(expected),
                        "observed": list(observed),
                    }
                )

    return score


def score_precision(document: Document, labels: dict[str, Any]) -> dict[str, Any]:
    exhaustive = set(labels["exhaustive_sections"])
    expected = Counter(
        normalise_marker(label["marker"])
        for label in labels["markers"]
        if label["section"] in exhaustive
    )

    observed: Counter[str] = Counter()
    for section in document.sections:
        if section.title not in exhaustive:
            continue
        for paragraph in section.paragraphs:
            for citation_id in paragraph.citation_ids:
                node = document.citations.get(citation_id)
                if node:
                    observed[normalise_marker(node.raw_marker)] += 1

    spurious = observed - expected
    return {
        "sections": sorted(exhaustive),
        "expected_markers": sum(expected.values()),
        "observed_markers": sum(observed.values()),
        "false_positives": sum(spurious.values()),
        "false_positive_markers": sorted(spurious.elements()),
        "precision": _ratio(
            sum(observed.values()) - sum(spurious.values()), sum(observed.values())
        ),
    }


def score_csl(document: Document, labels: dict[str, Any]) -> dict[str, Any]:
    checks = labels["bibliography"]["spot_checks"]
    by_order = {record.bibliography_order: record for record in document.references}
    by_title = {
        (record.csl.title or "").casefold(): record
        for record in document.references
        if record.csl.title
    }

    fields = Counter[str]()
    totals = Counter[str]()
    errors: list[dict[str, Any]] = []

    for check in checks:
        record = None
        if "number" in check:
            record = by_order.get(int(check["number"]))
        if record is None:
            record = by_title.get(str(check["title"]).casefold())
        if record is None:
            errors.append({"expected": check["title"], "problem": "entry not found"})
            continue

        for field_name, expected, observed in (
            ("title", check["title"], record.csl.title or ""),
            ("year", check["year"], record.csl.year),
            (
                "first_author_family",
                check["first_author_family"],
                (record.csl.author[0].get("family", "") if record.csl.author else ""),
            ),
        ):
            totals[field_name] += 1
            if _field_matches(field_name, expected, observed):
                fields[field_name] += 1
            else:
                errors.append(
                    {
                        "entry": check["title"][:60],
                        "field": field_name,
                        "expected": expected,
                        "observed": observed,
                    }
                )

    return {
        "entries_checked": len(checks),
        "field_accuracy": {
            name: _ratio(fields[name], totals[name]) for name in sorted(totals)
        },
        "errors": errors,
    }


def _field_matches(field_name: str, expected: Any, observed: Any) -> bool:
    if field_name == "year":
        return expected == observed
    left = re.sub(r"[^a-z0-9]+", " ", str(expected).casefold()).strip()
    right = re.sub(r"[^a-z0-9]+", " ", str(observed).casefold()).strip()
    if field_name == "title":
        return left == right

    return left in right.split() or left == right


def score_bibliography(
    document: Document, mapped_doc: MappedDocument, labels: dict[str, Any]
) -> dict[str, Any]:
    expected = labels["bibliography"]["entries"]
    tei_entries = mapped_doc.diagnostics.tei_bibl_entries
    return {
        "labelled_entries": expected,
        "tei_listbibl_entries": tei_entries,
        "retained_records": len(document.references),
        "retention_vs_label": (
            _ratio(len(document.references), expected) if expected else None
        ),
        "retention_vs_tei": _ratio(len(document.references), tei_entries),
        "note": labels["bibliography"]["counted_from"],
    }


def inventory(document: Document, labels: dict[str, Any]) -> dict[str, Any]:
    kinds = Counter(block.kind.value for block in document.blocks.values())
    return {
        "labelled_figures": labels["blocks"]["figures"],
        "labelled_tables": labels["blocks"]["tables"],
        "recovered": dict(sorted(kinds.items())),
        "figures_recovered": kinds.get(BlockKind.FIGURE.value, 0),
        "tables_recovered": kinds.get(BlockKind.TABLE.value, 0),
        "unrenderable": len(document.unrenderable_block_ids()),
        "floating": len(document.floating_block_ids),
        "note": labels["blocks"]["note"],
    }


def status_counts(document: Document) -> dict[str, Any]:
    occurrences = Counter(
        node.semantic_parse_status.value for node in document.citations.values()
    )
    items = sum(len(node.items) for node in document.citations.values())
    linked = sum(
        1
        for node in document.citations.values()
        for item in node.items
        if item.is_linked
    )
    references = Counter(
        record.normalization_status.value for record in document.references
    )
    return {
        "occurrences": len(document.citations),
        "occurrence_status": {
            status.value: occurrences.get(status.value, 0)
            for status in SemanticParseStatus
        },
        "items": items,
        "items_linked": linked,
        "item_linkage_coverage": _ratio(linked, items),
        "references": len(document.references),
        "reference_status": {
            status.value: references.get(status.value, 0)
            for status in NormalizationStatus
        },
    }


def postvalidation_effect(
    before: MappedDocument, after_report: PostValidationReport
) -> dict[str, Any]:
    codes = Counter(issue.code.value for issue in after_report.issues)
    return {
        "family_detected": after_report.family.value,
        "items_checked": after_report.checked,
        "agreed": after_report.agreed,
        "mismatched": after_report.mismatched,
        "independent_agreement_rate": _ratio(after_report.agreed, after_report.checked),
        "links_recovered": after_report.recovered,
        "occurrences_promoted": len(after_report.promoted_citation_ids),
        "occurrences_downgraded": len(after_report.downgraded_citation_ids),
        "left_ambiguous": after_report.ambiguous,
        "left_unresolved": after_report.unresolved,
        "flagged_uncertain": after_report.uncertain,
        "refused_as_not_a_marker": after_report.not_a_marker,
        "issue_codes": {code.value: codes.get(code.value, 0) for code in LinkageCode},
        "tei_bibr_refs": before.diagnostics.tei_bibr_refs,
        "refs_outside_mapped_prose": before.diagnostics.out_of_scope_refs,
        "clusters_formed": before.diagnostics.clusters,
        "brackets_absorbed": before.diagnostics.absorbed_openers,
        "author_debris_dropped": before.diagnostics.dropped_author_debris,
        "ranges_expanded": before.diagnostics.ranges_expanded,
        "modifiers_seen": before.diagnostics.modifiers_seen,
        "modifiers_attributed": before.diagnostics.modifiers_attributed,
    }


def evaluate(name: str) -> dict[str, Any]:
    labels = json.loads((LABELS / f"{name}.json").read_text())
    before = map_tei(parse_tei((TEI / f"{name}.tei.xml").read_bytes()))
    after = postvalidate(before)

    return {
        "paper": name,
        "title": labels["title"],
        "labelled_from": labels["labelled_from"],
        "scope_note": labels["scope_note"],
        "structure": {
            "sections": len(after.document.sections),
            "paragraphs": len(after.document.paragraphs()),
            "words": after.document.word_count(),
        },
        "markers_raw_grobid": score_markers(before.document, labels).as_dict(),
        "markers_postvalidated": score_markers(after.document, labels).as_dict(),
        "precision_postvalidated": score_precision(after.document, labels),
        "postvalidation_effect": postvalidation_effect(before, after.report),
        "status_raw_grobid": status_counts(before.document),
        "status_postvalidated": status_counts(after.document),
        "bibliography": score_bibliography(after.document, before, labels),
        "csl_fields": score_csl(after.document, labels),
        "blocks": inventory(after.document, labels),
    }


def _table(*columns: str) -> list[str]:
    return ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]


def render(results: list[dict[str, Any]]) -> str:
    lines = [
        "# Parser evaluation",
        "",
        f"Generated {datetime.now(UTC).isoformat(timespec='seconds')}.",
        "",
        "Labels were read from the published PDFs, never from this parser's own",
        "output. There is no pass threshold: three papers cannot support one.",
        "",
        "## Marker detection, clustering, and linkage against hand labels",
        "",
        *_table(
            "paper",
            "labelled",
            "detected (raw)",
            "detected (post)",
            "clusters right",
            "linkage right / checkable",
        ),
    ]
    for result in results:
        raw = result["markers_raw_grobid"]
        post = result["markers_postvalidated"]

        linkage = (
            f"{post['linkage_correct']} / {post['linkage_checkable']}"
            if post["linkage_checkable"]
            else "not measurable"
        )
        lines.append(
            f"| {result['paper']} | {post['labelled']} | {raw['detected']} | "
            f"{post['detected']} | {post['cluster_correct']} | {linkage} |"
        )
    lines += [
        "",
        "Linkage is scored against labels only where the printed marker carries a",
        "bibliography position. `B_author_year` has an unnumbered reference list, so",
        "its linkage is assessed by the independent author-and-year corroboration in",
        "the next table instead.",
    ]

    lines += [
        "",
        "## What postvalidation changed",
        "",
        *_table(
            "paper",
            "family",
            "checked",
            "agreed",
            "mismatched",
            "recovered",
            "promoted",
            "downgraded",
            "uncertain",
            "refused",
        ),
    ]
    for result in results:
        effect = result["postvalidation_effect"]
        lines.append(
            f"| {result['paper']} | {effect['family_detected']} | {effect['items_checked']} | "
            f"{effect['agreed']} | {effect['mismatched']} | {effect['links_recovered']} | "
            f"{effect['occurrences_promoted']} | {effect['occurrences_downgraded']} | "
            f"{effect['flagged_uncertain']} | {effect['refused_as_not_a_marker']} |"
        )

    lines += [
        "",
        "## Occurrence status, before and after",
        "",
        *_table(
            "paper",
            "STRUCTURED raw",
            "STRUCTURED post",
            "RAW_ONLY raw",
            "RAW_ONLY post",
            "PARTIAL_MODIFIERS",
            "item coverage",
        ),
    ]
    for result in results:
        raw = result["status_raw_grobid"]
        post = result["status_postvalidated"]
        lines.append(
            f"| {result['paper']} | {raw['occurrence_status']['STRUCTURED']} | "
            f"{post['occurrence_status']['STRUCTURED']} | "
            f"{raw['occurrence_status']['RAW_ONLY']} | "
            f"{post['occurrence_status']['RAW_ONLY']} | "
            f"{post['occurrence_status']['PARTIAL_MODIFIERS']} | "
            f"{post['item_linkage_coverage']} |"
        )

    lines += [
        "",
        "## Bibliography retention",
        "",
        *_table("paper", "labelled entries", "TEI listBibl", "retained records"),
    ]
    for result in results:
        bib = result["bibliography"]
        lines.append(
            f"| {result['paper']} | {bib['labelled_entries']} | "
            f"{bib['tei_listbibl_entries']} | {bib['retained_records']} |"
        )

    lines += [
        "",
        "## CSL field accuracy on hand-transcribed entries",
        "",
        *_table("paper", "entries", "title", "year", "first author"),
    ]
    for result in results:
        csl = result["csl_fields"]
        accuracy = csl["field_accuracy"]
        lines.append(
            f"| {result['paper']} | {csl['entries_checked']} | {accuracy.get('title')} | "
            f"{accuracy.get('year')} | {accuracy.get('first_author_family')} |"
        )

    lines += [
        "",
        "## Preserved blocks",
        "",
        *_table(
            "paper",
            "figures labelled / recovered",
            "tables labelled / recovered",
            "unrenderable",
            "position not preserved",
        ),
    ]
    for result in results:
        blocks = result["blocks"]
        lines.append(
            f"| {result['paper']} | {blocks['labelled_figures']} / {blocks['figures_recovered']} | "
            f"{blocks['labelled_tables']} / {blocks['tables_recovered']} | "
            f"{blocks['unrenderable']} | {blocks['floating']} |"
        )

    lines += ["", "## Precision inside exhaustively labelled sections", ""]
    for result in results:
        precision = result["precision_postvalidated"]
        lines.append(
            f"- **{result['paper']}** ({', '.join(precision['sections'])}): "
            f"{precision['observed_markers']} occurrences parsed against "
            f"{precision['expected_markers']} labelled, "
            f"{precision['false_positives']} not in the printed text "
            f"{precision['false_positive_markers']}."
        )

    lines += ["", "## Errors worth naming", ""]
    for result in results:
        for error in result["csl_fields"]["errors"]:
            lines.append(
                f"- **{result['paper']}** {error.get('field')}: expected "
                f"{error.get('expected')!r}, parsed {error.get('observed')!r} "
                f"({error.get('entry', '')})"
            )
        for wrong in result["markers_postvalidated"]["wrong_linkage"]:
            lines.append(
                f"- **{result['paper']}** {wrong['marker']} linked to "
                f"{wrong['observed']}, printed {wrong['expected']}"
            )
        for missing in result["markers_postvalidated"]["missing_markers"]:
            lines.append(f"- **{result['paper']}** marker not detected: {missing}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    RESULTS.mkdir(parents=True, exist_ok=True)
    names = sorted(path.stem for path in LABELS.glob("*.json"))
    results = [evaluate(name) for name in names]

    (RESULTS / "eval.json").write_text(json.dumps(results, indent=2) + "\n")
    report = render(results)
    (RESULTS / "eval.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
