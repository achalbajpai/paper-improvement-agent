from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.block import BlockKind
from app.domain.lifecycle import CitationStyle
from app.services.exporter.preflight import check
from app.services.parser.source_assets import attach_source_assets
from tests.conftest import validated


@pytest.mark.skipif(
    not Path("/evals/papers/A_numeric.pdf").exists(),
    reason="the corpus PDFs are mounted in the api container",
)
def test_embedded_raster_figures_are_recovered_from_the_source_pdf(tmp_path: Path) -> None:
    document = validated("A_numeric").document
    figures_before = [block for block in document.blocks.values() if block.kind is BlockKind.FIGURE]
    assert all(block.source_boxes for block in figures_before)
    attached = attach_source_assets(
        document,
        Path("/evals/papers/A_numeric.pdf"),
        tmp_path,
    )

    figures = [block for block in attached.blocks.values() if block.kind is BlockKind.FIGURE]
    assert len(figures) == 2
    assert all(block.source_asset for block in figures)
    assert all((tmp_path / block.source_asset).is_file() for block in figures if block.source_asset)
    assert not attached.unrenderable_block_ids()
    assert check(attached, revision_id="rev_1", citation_style=CitationStyle.IEEE).can_export
