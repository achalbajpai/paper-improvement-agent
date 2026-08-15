from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import DocumentRevision, ExportRun, Paper
from app.db.repositories import new_id
from app.domain.block import BlockKind, FidelityStatus, PreservedBlock
from app.domain.citation import (
    CitationItem,
    CitationMode,
    CitationNode,
    LocatorLabel,
    SemanticParseStatus,
)
from app.domain.document import Document
from app.domain.errors import (
    CitationRenderMismatchError,
    ErrorCode,
    ExportBlockedError,
    InvalidArtifactNameError,
    RenderFailedError,
)
from app.domain.lifecycle import CitationStyle, PaperStatus, RunStatus
from app.domain.verification import WarningCode
from app.services.deadline import Deadline
from app.services.exporter import pandoc_ir, preflight, render_set, renderer, service
from app.settings import get_settings
from tests.conftest import validated


def budget() -> Deadline:
    return Deadline.after("export", 180.0)


pandoc_only = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="pandoc lives in the api image; render tests run inside the container",
)


def corpus() -> Document:
    return validated("A_numeric").document


def exportable() -> Document:
    return validated("C_numeric_dense").document


def test_every_paragraph_and_occurrence_carries_its_id() -> None:
    document = exportable()
    ir = pandoc_ir.build(document)
    blob = json.dumps(ir)

    for paragraph in document.paragraphs()[:20]:
        assert f'"{paragraph.id}"' in blob

    signatures = pandoc_ir.citation_signatures(ir)
    placed = {
        citation_id
        for paragraph in document.paragraphs()
        for citation_id in paragraph.citation_ids
        if any(item.reference_id for item in document.citations[citation_id].items)
    }
    assert placed <= set(signatures)


def test_a_multi_item_marker_is_one_cite_not_two() -> None:
    document = corpus().model_copy(
        update={
            "citations": {
                "c1": CitationNode(
                    id="c1",
                    items=(
                        CitationItem(reference_id="r1", mode=CitationMode.NORMAL),
                        CitationItem(reference_id="r2", mode=CitationMode.NORMAL),
                    ),
                    raw_marker="[2, 5]",
                )
            }
        }
    )
    node = pandoc_ir._cite(document.citations["c1"])
    cite = node["c"][1][0]
    assert cite["t"] == "Cite"
    assert len(cite["c"][0]) == 2


def test_locators_travel_on_the_encoded_suffix() -> None:
    item = CitationItem(
        reference_id="r1",
        locator="17-19",
        locator_label=LocatorLabel.PAGE,
        mode=CitationMode.NORMAL,
    )
    node = CitationNode(id="c1", items=(item,), raw_marker="[1, pp. 17-19]")
    rendered = pandoc_ir._cite(node)
    suffix = rendered["c"][1][0]["c"][0][0]["citationSuffix"]
    assert "p." in json.dumps(suffix)
    assert "17-19" in json.dumps(suffix)


def test_an_unlinked_marker_keeps_its_literal_text() -> None:
    node = CitationNode(id="c9", items=(CitationItem(reference_id=None),), raw_marker="[42]")
    rendered = pandoc_ir._cite(node)
    assert "Cite" not in json.dumps(rendered)
    assert "42" in json.dumps(rendered)


def test_an_original_reference_that_falls_out_of_use_is_retained() -> None:
    document = exportable()
    cited = document.cited_reference_ids()
    original = frozenset(reference.id for reference in document.references)
    uncited = [reference.id for reference in document.references if reference.id not in cited]

    built = render_set.build(document, original_reference_ids=original)
    assert set(built.retained_uncited_ids) == set(uncited)

    meta = pandoc_ir.build(document, nocite_reference_ids=built.retained_uncited_ids)["meta"]
    named = {item["citationId"] for item in meta["nocite"]["c"][0]["c"][0]}
    assert named == set(uncited)


def test_an_added_reference_that_falls_out_of_use_is_not_retained() -> None:
    document = exportable()
    cited = document.cited_reference_ids()
    orphan = next(reference.id for reference in document.references if reference.id not in cited)

    original = frozenset(
        reference.id for reference in document.references if reference.id != orphan
    )

    built = render_set.build(document, original_reference_ids=original)
    assert orphan not in built.retained_uncited_ids
    assert orphan in render_set.dropped_added_reference_ids(
        document, original_reference_ids=original
    )


def test_csl_json_uses_csl_field_names() -> None:
    document = exportable()
    built = render_set.build(document, original_reference_ids=frozenset())
    blob = json.dumps(built.csl_json())
    assert "container_title" not in blob


def test_no_style_blocks_export() -> None:
    checks = preflight.check(exportable(), revision_id="rev_1", citation_style=None)
    assert not checks.can_export
    assert preflight.BLOCK_NO_STYLE_SELECTED in {b.code for b in checks.blockers}


def test_a_figure_with_no_image_blocks_a_fidelity_export() -> None:
    document = exportable()
    block = PreservedBlock(
        id="blk_x",
        kind=BlockKind.FIGURE,
        order_index=0,
        fidelity_status=FidelityStatus.UNRENDERABLE,
        missing_asset=True,
        caption="Figure 1",
    )
    with_block = document.model_copy(update={"blocks": {**document.blocks, "blk_x": block}})

    checks = preflight.check(with_block, revision_id="rev_1", citation_style=CitationStyle.IEEE)
    assert not checks.can_export
    assert preflight.BLOCK_UNRENDERABLE_BLOCK in {b.code for b in checks.blockers}


def test_the_corpus_paper_with_missing_figures_really_is_blocked() -> None:
    checks = preflight.check(corpus(), revision_id="rev_1", citation_style=CitationStyle.IEEE)
    assert not checks.can_export
    assert preflight.BLOCK_UNRENDERABLE_BLOCK in {b.code for b in checks.blockers}

    assert preflight.check(
        exportable(), revision_id="rev_1", citation_style=CitationStyle.IEEE
    ).can_export


def test_retained_entries_produce_an_acknowledgeable_warning() -> None:
    checks = preflight.check(
        exportable(),
        revision_id="rev_1",
        citation_style=CitationStyle.IEEE,
        retained_uncited_ids=("ref_imported_003",),
    )
    assert checks.can_export
    assert checks.required_warning_ids


def test_partial_modifiers_warn_rather_than_block() -> None:
    document = exportable()
    checks = preflight.check(document, revision_id="rev_1", citation_style=CitationStyle.IEEE)

    assert document.non_exportable_citation_ids()
    assert checks.can_export
    assert checks.required_warning_ids


def test_the_two_kinds_of_unstructured_marker_are_reported_separately() -> None:
    document = exportable()
    raw_only = document.citations_by_parse_status(SemanticParseStatus.RAW_ONLY)
    partial = document.citations_by_parse_status(SemanticParseStatus.PARTIAL_MODIFIERS)
    assert raw_only and not partial

    messages = [
        warning.message
        for warning in preflight.check(
            document, revision_id="rev_1", citation_style=CitationStyle.IEEE
        ).warnings
    ]
    assert any("could not be parsed into structured citations" in text for text in messages)
    assert not any("Page numbers or notes" in text for text in messages)


def test_warning_messages_are_grammatical_for_a_single_subject() -> None:
    checks = preflight.check(
        exportable(),
        revision_id="rev_1",
        citation_style=CitationStyle.IEEE,
        retained_uncited_ids=("ref_imported_003",),
    )
    retained = next(
        warning for warning in checks.warnings if warning.code is WarningCode.REFERENCE_LEFT_UNCITED
    )
    assert retained.message.startswith("1 original bibliography entry is no longer cited")


def test_warning_ids_are_stable_across_identical_checks() -> None:
    first = preflight.check(exportable(), revision_id="rev_1", citation_style=CitationStyle.IEEE)
    second = preflight.check(exportable(), revision_id="rev_1", citation_style=CitationStyle.IEEE)
    assert first.required_warning_ids == second.required_warning_ids


@pandoc_only
@pytest.mark.parametrize("style", [CitationStyle.IEEE, CitationStyle.APA])
def test_the_manuscript_renders_through_both_styles(tmp_path: Path, style: CitationStyle) -> None:
    document = exportable()
    built = render_set.build(
        document,
        original_reference_ids=frozenset(reference.id for reference in document.references),
    )
    artifacts = renderer.render(
        document, built, style=style, into=tmp_path / style.value, deadline=budget()
    )

    assert {artifact.name for artifact in artifacts} == set(renderer.ARTIFACT_NAMES)
    assert all(artifact.size_bytes > 0 for artifact in artifacts)
    assert (tmp_path / style.value / "revised.pdf").read_bytes()[:4] == b"%PDF"


@pandoc_only
def test_a_retained_entry_appears_in_the_rendered_bibliography(tmp_path: Path) -> None:
    document = exportable()
    built = render_set.build(
        document,
        original_reference_ids=frozenset(reference.id for reference in document.references),
    )
    if not built.retained_uncited_ids:
        pytest.skip("this corpus paper cites every entry")

    with_nocite = tmp_path / "with"
    renderer.render(document, built, style=CitationStyle.IEEE, into=with_nocite, deadline=budget())

    without = render_set.RenderSet(references=built.references, retained_uncited_ids=())
    renderer.render(
        document, without, style=CitationStyle.IEEE, into=tmp_path / "without", deadline=budget()
    )

    kept = (with_nocite / "revised.md").read_text(encoding="utf-8")
    dropped = (tmp_path / "without" / "revised.md").read_text(encoding="utf-8")
    assert len(kept) > len(dropped)


@pandoc_only
def test_a_tampered_ir_fails_the_signature_check(tmp_path: Path, monkeypatch) -> None:
    document = exportable()
    built = render_set.build(document, original_reference_ids=frozenset())
    real_build = pandoc_ir.build

    def lossy(doc: Document, **kwargs: object) -> dict[str, object]:
        ir = real_build(doc, **kwargs)
        _drop_first_cite(ir)
        return ir

    monkeypatch.setattr(renderer.pandoc_ir, "build", lossy)
    with pytest.raises(CitationRenderMismatchError):
        renderer.render(
            document, built, style=CitationStyle.IEEE, into=tmp_path / "lossy", deadline=budget()
        )


@pytest.fixture
def export_paper(db: Session) -> Paper:
    document = exportable()
    paper = Paper(
        id=new_id("paper"),
        storage_id=new_id("store"),
        original_filename="C_numeric_dense.pdf",
        content_sha256="1" * 64,
        size_bytes=2048,
        status=PaperStatus.PARSED.value,
        title=document.title or None,
        citation_style=CitationStyle.IEEE.value,
    )
    db.add(paper)
    db.flush()
    revision = DocumentRevision(
        id=new_id("rev"),
        paper_id=paper.id,
        revision_number=1,
        document=document.model_dump(mode="json"),
        content_sha256=document.content_hash(),
        segmenter_version=document.segmenter_version,
    )
    db.add(revision)
    paper.current_revision_id = revision.id
    db.flush()
    return paper


def test_an_unacknowledged_lossy_export_is_refused(db: Session, export_paper: Paper) -> None:
    checks = service.preflight(db, export_paper.id)
    assert checks.required_warning_ids

    with pytest.raises(ExportBlockedError):
        service.run_export(db, export_paper.id, acknowledged_warning_ids=[])


@pandoc_only
def test_an_acknowledged_export_publishes_every_artifact(db: Session, export_paper: Paper) -> None:
    checks = service.preflight(db, export_paper.id)
    run = service.run_export(
        db, export_paper.id, acknowledged_warning_ids=list(checks.required_warning_ids)
    )

    assert run.status == RunStatus.COMPLETED.value
    assert {artifact["name"] for artifact in run.artifacts} == set(renderer.ARTIFACT_NAMES)
    for name in renderer.ARTIFACT_NAMES:
        assert service.artifact_path(db, run.id, name).stat().st_size > 0
    assert service.artifact_path(db, run.id, "revised.pdf").read_bytes()[:4] == b"%PDF"


@pandoc_only
def test_nothing_is_visible_until_the_whole_export_is_ready(
    db: Session, export_paper: Paper, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = service.preflight(db, export_paper.id)
    monkeypatch.setattr(
        renderer,
        "_pandoc",
        _fail_on("revised.pdf", renderer._pandoc),
    )

    with pytest.raises(RenderFailedError):
        service.run_export(
            db, export_paper.id, acknowledged_warning_ids=list(checks.required_warning_ids)
        )

    run = db.query(ExportRun).filter_by(paper_id=export_paper.id).one()
    assert run.status == RunStatus.FAILED.value
    assert run.failure_code == ErrorCode.RENDER_FAILED.value
    assert not run.artifacts
    published = get_settings().papers_dir / export_paper.storage_id / "exports" / run.id
    assert not published.exists()


def test_an_unknown_artifact_name_is_refused(db: Session, export_paper: Paper) -> None:
    run = ExportRun(
        id=new_id("exp"),
        paper_id=export_paper.id,
        revision_id=str(export_paper.current_revision_id),
        citation_style=CitationStyle.IEEE.value,
        status=RunStatus.COMPLETED.value,
    )
    db.add(run)
    db.flush()

    for name in ("../../../../etc/passwd", "original.pdf", "revised.md.bak"):
        with pytest.raises(InvalidArtifactNameError):
            service.artifact_path(db, run.id, name)


def _fail_on(name: str, real):
    def wrapper(arguments: list[str], source: Path, target: Path, deadline: Deadline) -> None:
        if target.name == name:
            raise RenderFailedError("Simulated engine failure.", target=name)
        real(arguments, source, target, deadline)

    return wrapper


def _drop_first_cite(node: object) -> bool:
    if isinstance(node, list):
        for index, item in enumerate(node):
            if isinstance(item, dict) and item.get("t") == "Span":
                inlines = item["c"][1]
                if any(isinstance(x, dict) and x.get("t") == "Cite" for x in inlines):
                    del node[index]
                    return True
            if _drop_first_cite(item):
                return True
    elif isinstance(node, dict):
        for value in node.values():
            if _drop_first_cite(value):
                return True
    return False


@pandoc_only
@pytest.mark.parametrize("style", [CitationStyle.IEEE, CitationStyle.APA])
def test_the_rendered_output_carries_the_citations_and_the_bibliography(
    tmp_path: Path, style: CitationStyle
) -> None:
    document = exportable()
    built = render_set.build(
        document,
        original_reference_ids=frozenset(reference.id for reference in document.references),
    )
    into = tmp_path / style.value
    renderer.render(document, built, style=style, into=into, deadline=budget())

    markdown = (into / "revised.md").read_text(encoding="utf-8")
    latex = (into / "revised.tex").read_text(encoding="utf-8")

    cited = document.cited_reference_ids()
    assert cited, "this corpus paper cites nothing"

    titles = [
        reference.csl.title
        for reference in document.references
        if reference.id in cited and reference.csl.title
    ]
    assert titles, "no cited reference carries a title to look for"

    for rendered in (markdown, latex):
        present = sum(1 for title in titles if _fingerprint(title) in _fingerprint(rendered))
        assert present >= max(1, len(titles) // 2), (
            f"{style.value}: the rendered bibliography lost most of its entries"
        )

    for rendered, marker in ((markdown, "citation marker"), (latex, "citation marker")):
        assert _citation_markers(rendered) > 0, f"{style.value}: no {marker} survived rendering"


@pandoc_only
def test_a_style_change_changes_the_rendered_citations(tmp_path: Path) -> None:
    document = exportable()
    built = render_set.build(
        document,
        original_reference_ids=frozenset(reference.id for reference in document.references),
    )
    renderer.render(
        document, built, style=CitationStyle.IEEE, into=tmp_path / "ieee", deadline=budget()
    )
    renderer.render(
        document, built, style=CitationStyle.APA, into=tmp_path / "apa", deadline=budget()
    )

    ieee = (tmp_path / "ieee" / "revised.md").read_text(encoding="utf-8")
    apa = (tmp_path / "apa" / "revised.md").read_text(encoding="utf-8")
    assert ieee != apa


def _fingerprint(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold())


def _citation_markers(rendered: str) -> int:
    numeric = len(re.findall(r"(?:\\\[|\[)\d+(?:\\\]|\])", rendered))
    author_year = len(re.findall(r"\(\s*[A-Z][^()]*\d{4}[a-z]?\s*\)", rendered))
    return numeric + author_year
