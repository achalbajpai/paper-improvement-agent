from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from app.domain.document import Document
from app.domain.errors import CitationRenderMismatchError, RenderFailedError
from app.domain.lifecycle import CitationStyle
from app.services.deadline import Deadline
from app.services.exporter import pandoc_ir
from app.services.exporter.render_set import RenderSet
from app.settings import get_settings

STYLES = Path(__file__).resolve().parent / "styles"


ARTIFACT_NAMES = ("revised.md", "references.json", "revised.tex", "revised.pdf")


PDF_ENGINE = "xelatex"


@dataclass(frozen=True)
class Artifact:
    name: str
    media_type: str
    size_bytes: int


MEDIA_TYPES = {
    ".md": "text/markdown",
    ".json": "application/json",
    ".tex": "application/x-tex",
    ".pdf": "application/pdf",
}


def style_path(style: CitationStyle) -> Path:
    path = STYLES / f"{style.value.lower()}.csl"
    if not path.exists():
        raise RenderFailedError("The citation style file is missing.", style=style.value)
    return path


def render(
    document: Document,
    render_set: RenderSet,
    *,
    style: CitationStyle,
    into: Path,
    deadline: Deadline,
    asset_root: Path | None = None,
) -> list[Artifact]:
    deadline.check("render")
    into.mkdir(parents=True, exist_ok=True)
    render_document = _materialize_assets(document, asset_root=asset_root, into=into)
    ir = pandoc_ir.build(render_document, nocite_reference_ids=render_set.retained_uncited_ids)

    ir_path = into / "document.json"
    bibliography = into / "references.json"
    ir_path.write_text(json.dumps(ir), encoding="utf-8")
    bibliography.write_text(
        json.dumps(render_set.csl_json(), indent=2, ensure_ascii=False), encoding="utf-8"
    )

    common = [
        "--from=json",
        "--citeproc",
        f"--bibliography={bibliography}",
        f"--csl={style_path(style)}",
        f"--resource-path={into}",
    ]
    _verify_document_ir(render_document, ir)
    _verify_citations(render_document, ir_path, common, into, deadline)

    _pandoc([*common, "--to=markdown", "--wrap=preserve"], ir_path, into / "revised.md", deadline)
    _pandoc([*common, "--to=latex", "--standalone"], ir_path, into / "revised.tex", deadline)
    _pandoc(
        [*common, "--to=pdf", f"--pdf-engine={PDF_ENGINE}"], ir_path, into / "revised.pdf", deadline
    )
    _verify_pdf(render_document, into / "revised.pdf")

    ir_path.unlink()
    return [_artifact(into / name) for name in ARTIFACT_NAMES]


def _materialize_assets(document: Document, *, asset_root: Path | None, into: Path) -> Document:
    blocks = dict(document.blocks)
    asset_dir = into / "assets"
    copied = False
    for block_id, block in document.blocks.items():
        if not block.source_asset:
            continue
        if asset_root is None:
            raise RenderFailedError(
                "This export references a source crop but its manuscript storage is unavailable.",
                target=block_id,
            )
        source = Path(block.source_asset)
        if not source.is_absolute():
            source = asset_root / source
        if not source.exists() or not source.is_file():
            raise RenderFailedError(
                "A source crop referenced by the manuscript is missing.",
                target=block_id,
            )
        asset_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{block_id}{source.suffix.lower() or '.png'}"
        shutil.copy2(source, asset_dir / filename)
        blocks[block_id] = block.model_copy(update={"source_asset": f"assets/{filename}"})
        copied = True

    if not copied:
        return document
    return document.model_copy(update={"blocks": blocks})


def _verify_document_ir(document: Document, ir: dict[str, object]) -> None:
    meta = ir.get("meta")
    if not isinstance(meta, dict):
        raise RenderFailedError("Pandoc IR has no metadata.", target="document.json")
    if document.title:
        title = meta.get("title")
        if title != {"t": "MetaString", "c": document.title}:
            raise RenderFailedError(
                "The export IR lost the manuscript title.", target="document.json"
            )
    if document.authors and "author" not in meta:
        raise RenderFailedError("The export IR lost manuscript authors.", target="document.json")
    if document.abstract and "abstract" not in meta:
        raise RenderFailedError(
            "The export IR lost the manuscript abstract.", target="document.json"
        )

    block_ids = _div_ids(ir.get("blocks", []))
    expected = set(document.blocks)
    if expected - block_ids:
        raise RenderFailedError(
            "The export IR dropped one or more preserved blocks.",
            target="document.json",
            missing=sorted(expected - block_ids),
        )


def _div_ids(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, list):
        for item in node:
            found.update(_div_ids(item))
    elif isinstance(node, dict):
        if node.get("t") == "Div":
            content = node.get("c")
            if isinstance(content, list) and content and isinstance(content[0], list):
                attr = content[0]
                if attr and isinstance(attr[0], str):
                    found.add(attr[0])
        for value in node.values():
            found.update(_div_ids(value))
    return found


def _verify_pdf(document: Document, path: Path) -> None:
    try:
        reader = PdfReader(str(path))
        if not reader.pages:
            raise ValueError("PDF contains no pages")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise RenderFailedError(
            "The rendered PDF could not be inspected.", target=path.name
        ) from exc

    normalized = " ".join(text.split()).casefold()
    required = [document.title, *(author.name for author in document.authors)]
    missing = [
        value
        for value in required
        if value and " ".join(value.split()).casefold() not in normalized
    ]
    if missing:
        raise RenderFailedError(
            "The rendered PDF is missing manuscript metadata.",
            target=path.name,
            missing=missing,
        )


def _verify_citations(
    document: Document, ir_path: Path, common: list[str], into: Path, deadline: Deadline
) -> None:
    round_tripped = into / "roundtrip.json"
    _pandoc(["--from=json", "--to=json"], ir_path, round_tripped, deadline)
    rendered = pandoc_ir.citation_signatures(json.loads(round_tripped.read_text(encoding="utf-8")))
    round_tripped.unlink()

    expected = {
        citation_id: [item.signature() for item in node.items if item.reference_id]
        for citation_id, node in document.citations.items()
        if any(item.reference_id for item in node.items) and citation_id in _placed(document)
    }

    missing = sorted(set(expected) - set(rendered))
    changed = sorted(
        citation_id
        for citation_id, signatures in expected.items()
        if citation_id in rendered and rendered[citation_id] != signatures
    )
    if missing or changed:
        raise CitationRenderMismatchError(
            "The rendered document does not carry the citations the manuscript has.",
            missing=missing,
            changed=changed,
        )


def _placed(document: Document) -> set[str]:
    return {
        citation_id for paragraph in document.paragraphs() for citation_id in paragraph.citation_ids
    }


def _pandoc(arguments: list[str], source: Path, target: Path, deadline: Deadline) -> None:
    executable = shutil.which("pandoc")
    if executable is None:
        raise RenderFailedError(
            "Pandoc is not available in this environment, so export cannot run."
        )

    command = [executable, *arguments, "--output", str(target), str(source)]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            timeout=deadline.budget_for(get_settings().pandoc_timeout_seconds),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RenderFailedError("Rendering exceeded its time budget.", target=target.name) from exc

    if completed.returncode != 0:
        raise RenderFailedError(
            "Pandoc failed to render this document.",
            target=target.name,
            detail=completed.stderr.decode("utf-8", "replace")[-400:],
        )
    if not target.exists() or target.stat().st_size == 0:
        raise RenderFailedError("Pandoc produced an empty file.", target=target.name)


def _artifact(path: Path) -> Artifact:
    return Artifact(
        name=path.name,
        media_type=MEDIA_TYPES.get(path.suffix, "application/octet-stream"),
        size_bytes=path.stat().st_size,
    )
