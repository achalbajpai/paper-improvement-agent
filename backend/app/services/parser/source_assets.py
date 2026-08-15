"""Recover visual source assets for non-prose blocks.

GROBID gives us logical TEI and, when requested, coordinates in the original
PDF. It does not give the exporter trustworthy TeX for formulas or image bytes
for every figure. This module keeps those concerns separate: source crops are
derived from the uploaded PDF, never guessed from flattened TEI text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pymupdf

from app.domain.block import BlockKind, FidelityStatus
from app.domain.document import Document
from app.observability import get_logger

logger = get_logger(__name__)

_CROP_KINDS = {BlockKind.FIGURE, BlockKind.TABLE, BlockKind.FORMULA}
_DPI = 300
_DRAWING_GAP = 24
_PYMUPDF_OPEN = cast(Any, pymupdf.open)
_PYMUPDF_RECT = cast(Any, pymupdf.Rect)


def attach_source_assets(document: Document, pdf_path: Path, storage_root: Path) -> Document:
    """Render coordinate-backed blocks from the original PDF.

    A block is cropped only when all of its coordinates are on one page. A
    multi-page or malformed coordinate set is left untouched and remains
    visible to export preflight as a reconstructed/unrenderable block.
    """
    candidates = [
        block
        for block in document.blocks.values()
        if block.kind in _CROP_KINDS and block.source_boxes and not block.source_asset
    ]
    if not candidates or not pdf_path.exists():
        return document

    assets_dir = storage_root / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    updates = dict(document.blocks)

    try:
        source_pdf = _PYMUPDF_OPEN(str(pdf_path))
    except Exception as exc:
        logger.warning("source_assets.open_failed", extra={"detail": str(exc)[:200]})
        return document

    with source_pdf:
        for block in candidates:
            pages = {box.page for box in block.source_boxes}
            if len(pages) != 1:
                continue
            page_number = next(iter(pages))
            if page_number < 1 or page_number > len(source_pdf):
                continue

            page = source_pdf[page_number - 1]
            source_rect = _PYMUPDF_RECT(
                min(box.x for box in block.source_boxes),
                min(box.y for box in block.source_boxes),
                max(box.x + box.width for box in block.source_boxes),
                max(box.y + box.height for box in block.source_boxes),
            )
            clip = (
                _figure_image_clip(page, source_rect) or _figure_drawing_clip(page, source_rect)
                if block.kind is BlockKind.FIGURE
                else _table_content_clip(page, source_rect)
                if block.kind is BlockKind.TABLE
                else source_rect
            )
            if clip is None:
                continue
            clip &= page.rect
            if clip.is_empty or clip.width <= 0 or clip.height <= 0:
                continue

            filename = f"{block.id}.png"
            target = assets_dir / filename
            try:
                pixmap = page.get_pixmap(dpi=_DPI, clip=clip, alpha=False)
                cast(Any, pixmap.save)(str(target))
            except Exception as exc:
                logger.warning(
                    "source_assets.crop_failed",
                    extra={"block_id": block.id, "detail": str(exc)[:200]},
                )
                continue

            updates[block.id] = block.model_copy(
                update={
                    "source_asset": f"assets/{filename}",
                    "fidelity_status": FidelityStatus.SOURCE_CROP,
                    "missing_asset": False,
                }
            )

    return document.model_copy(update={"blocks": updates})


def _figure_image_clip(page: pymupdf.Page, source: pymupdf.Rect) -> pymupdf.Rect | None:
    """Recover raster figures whose image blocks are embedded in the PDF.

    GROBID's ``graphic`` coordinates are the image rectangle for bitmap figures,
    not a path returned by ``Page.get_drawings``.  The old vector-only lookup
    therefore marked valid raster figures as missing.  Include image blocks
    that overlap the supplied rectangle and sibling subfigures in the same
    vertical band (for example the two panels of Figure 2 in A_numeric).
    """
    image_rects: list[pymupdf.Rect] = []
    try:
        blocks = cast(Any, page.get_text)("dict").get("blocks", [])
    except Exception:
        blocks = []

    for block in blocks:
        if block.get("type") != 1 or "bbox" not in block:
            continue
        rect = _PYMUPDF_RECT(block["bbox"])
        if rect.width <= 0 or rect.height <= 0:
            continue
        if _rects_overlap(rect, source) or _vertical_overlap_ratio(rect, source) >= 0.6:
            image_rects.append(rect)

    if not image_rects:
        try:
            images = cast(Any, page.get_images)(full=True)
            for image in images:
                for rect in cast(Any, page.get_image_rects)(image[0]):
                    if _rects_overlap(rect, source) or _vertical_overlap_ratio(rect, source) >= 0.6:
                        image_rects.append(rect)
        except Exception:
            return None

    if not image_rects:
        return None
    return cast(
        pymupdf.Rect,
        _PYMUPDF_RECT(
            min(rect.x0 for rect in image_rects),
            min(rect.y0 for rect in image_rects),
            max(rect.x1 for rect in image_rects),
            max(rect.y1 for rect in image_rects),
        ),
    )


def _rects_overlap(left: pymupdf.Rect, right: pymupdf.Rect) -> bool:
    return left.x1 > right.x0 and left.x0 < right.x1 and left.y1 > right.y0 and left.y0 < right.y1


def _vertical_overlap_ratio(left: pymupdf.Rect, right: pymupdf.Rect) -> float:
    overlap = max(0.0, min(left.y1, right.y1) - max(left.y0, right.y0))
    shorter = min(left.height, right.height)
    return overlap / shorter if shorter > 0 else 0.0


def _figure_drawing_clip(page: pymupdf.Page, caption: pymupdf.Rect) -> pymupdf.Rect | None:
    """Find the vector-art cluster immediately above a figure caption."""
    candidates: list[pymupdf.Rect] = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return None

    for drawing in drawings:
        rect = drawing.get("rect")
        if not isinstance(rect, pymupdf.Rect):
            continue
        if rect.width < 10 or rect.height < 5:
            continue
        if rect.y1 > caption.y0 + 3:
            continue
        if rect.x1 < caption.x0 - 4 or rect.x0 > caption.x1 + 4:
            continue
        candidates.append(rect)

    if not candidates:
        return None

    clusters: list[list[pymupdf.Rect]] = []
    for rect in sorted(candidates, key=lambda item: (item.y0, item.x0)):
        if not clusters:
            clusters.append([rect])
            continue
        current = clusters[-1]
        current_bottom = max(item.y1 for item in current)
        if rect.y0 - current_bottom <= _DRAWING_GAP:
            current.append(rect)
        else:
            clusters.append([rect])

    cluster = max(
        clusters,
        key=lambda items: (
            max(item.y1 for item in items),
            sum(item.width * item.height for item in items),
        ),
    )
    cluster_x0 = min(item.x0 for item in cluster)
    cluster_y0 = min(item.y0 for item in cluster)
    cluster_x1 = max(item.x1 for item in cluster)
    artwork_y0 = _figure_artwork_top(page, caption, cluster_y0)
    return cast(
        pymupdf.Rect,
        _PYMUPDF_RECT(
            min(caption.x0, cluster_x0),
            artwork_y0,
            max(caption.x1, cluster_x1),
            caption.y0 - 1,
        ),
    )


def _figure_artwork_top(page: pymupdf.Page, caption: pymupdf.Rect, cluster_y0: float) -> float:
    """Include labels that sit just above the vector drawing cluster."""
    artwork_y0 = cluster_y0
    try:
        text_blocks = cast(Any, page.get_text)("dict").get("blocks", [])
    except Exception:
        text_blocks = []
    for block in text_blocks:
        if block.get("type") != 0 or "bbox" not in block:
            continue
        text_rect = _PYMUPDF_RECT(block["bbox"])
        if text_rect.height > 25:
            continue
        if text_rect.y1 > caption.y0 or text_rect.y1 < cluster_y0 - 50:
            continue
        if text_rect.x1 < caption.x0 - 4 or text_rect.x0 > caption.x1 + 4:
            continue
        artwork_y0 = min(artwork_y0, text_rect.y0)
    return artwork_y0


def _table_content_clip(page: pymupdf.Page, source: pymupdf.Rect) -> pymupdf.Rect:
    """Keep a table crop's caption in the semantic caption, not in the image."""
    bottom = source.y1
    try:
        text_blocks = cast(Any, page.get_text)("dict").get("blocks", [])
    except Exception:
        text_blocks = []
    for block in text_blocks:
        if block.get("type") != 0 or "bbox" not in block:
            continue
        text = "".join(
            span.get("text", "")
            for line in block.get("lines", [])
            for span in line.get("spans", [])
        ).strip()
        rect = _PYMUPDF_RECT(block["bbox"])
        if not text.lower().startswith("table"):
            continue
        if rect.y0 < source.y0 or rect.y0 > source.y1:
            continue
        if rect.x1 < source.x0 or rect.x0 > source.x1:
            continue
        bottom = min(bottom, rect.y0 - 1)
    return cast(pymupdf.Rect, _PYMUPDF_RECT(source.x0, source.y0, source.x1, bottom))


__all__ = ["attach_source_assets"]
