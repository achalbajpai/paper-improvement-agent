from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import httpx

CORPUS_DIR = Path("/evals/papers")


@dataclass(frozen=True)
class CorpusPaper:
    key: str
    arxiv_id: str
    title: str
    expected_family: str
    note: str


CORPUS: tuple[CorpusPaper, ...] = (
    CorpusPaper(
        key="A_numeric",
        arxiv_id="1706.03762",
        title="Attention Is All You Need",
        expected_family="NUMERIC",
        note="NeurIPS style, bracketed numeric markers, single column.",
    ),
    CorpusPaper(
        key="B_author_year",
        arxiv_id="1810.04805",
        title="BERT: Pre-training of Deep Bidirectional Transformers",
        expected_family="AUTHOR_YEAR",
        note="ACL style, two column, parenthetical and narrative author-year.",
    ),
    CorpusPaper(
        key="C_numeric_dense",
        arxiv_id="1512.03385",
        title="Deep Residual Learning for Image Recognition",
        expected_family="NUMERIC",
        note="CVPR style, dense two column, many figures and tables.",
    ),
)


def fetch(paper: CorpusPaper, directory: Path) -> dict[str, object]:
    destination = directory / f"{paper.key}.pdf"
    if destination.exists():
        content = destination.read_bytes()
    else:
        url = f"https://arxiv.org/pdf/{paper.arxiv_id}"
        with httpx.Client(follow_redirects=True, timeout=120.0) as client:
            response = client.get(url, headers={"User-Agent": "answerthis-assessment/0.1"})
            response.raise_for_status()
            content = response.content
        if not content.startswith(b"%PDF-"):
            raise RuntimeError(f"{url} did not return a PDF")
        destination.write_bytes(content)

    return {
        "key": paper.key,
        "arxiv_id": paper.arxiv_id,
        "title": paper.title,
        "expected_family": paper.expected_family,
        "note": paper.note,
        "filename": destination.name,
        "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def main() -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    entries = [fetch(paper, CORPUS_DIR) for paper in CORPUS]
    manifest = CORPUS_DIR / "corpus.json"
    manifest.write_text(json.dumps({"papers": entries}, indent=2) + "\n", encoding="utf-8")
    for entry in entries:
        print(f"{entry['key']:20} {entry['bytes']:>9,} bytes  sha256={str(entry['sha256'])[:16]}…")
    print(f"\nwrote {manifest}")


if __name__ == "__main__":
    main()
