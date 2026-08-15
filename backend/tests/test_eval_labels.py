"""The hand labels, as a regression guard.

The evaluation is a report, not a gate -- three papers cannot support a pass
threshold. But a parser change that stops detecting a marker somebody read off
the printed page is a regression whatever the report says, so the labelled
markers are checked on every test run rather than only when the eval is invoked.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

EVALS = Path("/evals")
LABELS = EVALS / "labels"

pytestmark = pytest.mark.skipif(
    not LABELS.exists(), reason="evals/ is mounted only in the api container"
)


def _run_eval() -> Any:
    """Import the harness from outside the package, as ``make eval`` runs it.

    ``evals/`` is not on the import path, so the module is loaded by location and
    registered in ``sys.modules`` first: ``dataclasses`` resolves annotations
    through the module entry, and an unregistered module makes every dataclass in
    the file fail to build.
    """
    spec = importlib.util.spec_from_file_location("run_eval", EVALS / "run_eval.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("run_eval.py could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def results() -> list[dict[str, Any]]:
    module = _run_eval()
    names = sorted(path.stem for path in LABELS.glob("*.json"))
    return [module.evaluate(name) for name in names]


def test_labels_are_well_formed() -> None:
    for path in LABELS.glob("*.json"):
        labels = json.loads(path.read_text())
        assert labels["markers"], f"{path.name} has no labelled markers"
        assert labels["labelled_from"].startswith("The published PDF")
        for marker in labels["markers"]:
            assert marker["marker"].strip()
            assert marker["works"]
            assert marker["mode"] in {"NORMAL", "AUTHOR_IN_TEXT", "SUPPRESS_AUTHOR"}


def test_every_numeric_labelled_marker_is_detected_and_linked(
    results: list[dict[str, Any]],
) -> None:
    """The headline claim, checked rather than asserted in prose."""
    for result in results:
        score = result["markers_postvalidated"]
        if not score["linkage_checkable"]:
            continue
        assert score["missing_markers"] == []
        assert score["wrong_linkage"] == []
        assert score["detected"] == score["labelled"]
        assert score["linkage_correct"] == score["linkage_checkable"]


def test_clusters_survive_for_every_detected_marker(results: list[dict[str, Any]]) -> None:
    for result in results:
        score = result["markers_postvalidated"]
        assert score["cluster_correct"] == score["detected"]


def test_no_bibliography_entry_is_lost(results: list[dict[str, Any]]) -> None:
    for result in results:
        bibliography = result["bibliography"]
        assert bibliography["retained_records"] == bibliography["tei_listbibl_entries"]
        if bibliography["labelled_entries"] is not None:
            assert bibliography["retained_records"] == bibliography["labelled_entries"]


def test_postvalidation_never_reduces_structured_occurrences(
    results: list[dict[str, Any]],
) -> None:
    """On this corpus it only recovers. A change that made it destructive would
    show up here before it showed up in an export."""
    for result in results:
        raw = result["status_raw_grobid"]["occurrence_status"]["STRUCTURED"]
        post = result["status_postvalidated"]["occurrence_status"]["STRUCTURED"]
        assert post >= raw
