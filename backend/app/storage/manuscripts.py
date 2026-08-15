from __future__ import annotations

import hashlib
import shutil
import time
import uuid
from pathlib import Path

from app.domain.errors import FileTooLargeError, UnsupportedFileTypeError
from app.settings import get_settings

PDF_MAGIC = b"%PDF-"


def validate_pdf(content: bytes) -> None:
    settings = get_settings()
    if len(content) > settings.max_upload_bytes:
        raise FileTooLargeError(
            "The uploaded file exceeds the size limit.",
            limit_bytes=settings.max_upload_bytes,
            actual_bytes=len(content),
        )
    if not content.startswith(PDF_MAGIC):
        raise UnsupportedFileTypeError("Only PDF uploads are supported.")


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def new_storage_dir() -> tuple[str, Path]:
    storage_id = str(uuid.uuid4())
    directory = get_settings().papers_dir / storage_id
    directory.mkdir(parents=True, exist_ok=False)
    return storage_id, directory


def store_pdf(directory: Path, content: bytes) -> Path:
    path = directory / "original.pdf"
    path.write_bytes(content)
    return path


def tei_path(directory: Path) -> Path:
    return directory / "grobid.tei.xml"


def exports_dir(directory: Path) -> Path:
    path = directory / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def discard_directory(directory: Path) -> None:
    shutil.rmtree(directory, ignore_errors=True)


def move_to_trash(directory: Path) -> Path | None:
    if not directory.exists():
        return None
    destination = get_settings().trash_dir / f"{directory.name}-{uuid.uuid4().hex[:8]}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(directory), str(destination))
    return destination


def purge_trash_entry(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def sweep_trash() -> int:
    trash = get_settings().trash_dir
    if not trash.exists():
        return 0
    removed = 0
    cutoff = time.time() - get_settings().trash_retention_seconds
    for entry in trash.iterdir():
        if entry.is_symlink() or not entry.is_dir() or entry.stat().st_mtime > cutoff:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed += 1
    return removed


def quarantine_orphaned_papers(known_storage_ids: set[str]) -> int:
    root = get_settings().papers_dir
    if not root.exists():
        return 0
    quarantined = 0
    for entry in root.iterdir():
        if not entry.is_dir() or entry.is_symlink() or entry.name in known_storage_ids:
            continue
        try:
            generated_name = str(uuid.UUID(entry.name))
        except ValueError:
            continue
        if generated_name != entry.name:
            continue
        if move_to_trash(entry) is not None:
            quarantined += 1
    return quarantined
