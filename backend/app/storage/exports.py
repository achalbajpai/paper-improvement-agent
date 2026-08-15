from __future__ import annotations

import shutil
from pathlib import Path

from app.domain.errors import InvalidArtifactNameError
from app.services.exporter.renderer import ARTIFACT_NAMES
from app.storage.manuscripts import exports_dir

TMP = ".tmp"


def staging_dir(storage_dir: Path, run_id: str) -> Path:
    path = exports_dir(storage_dir) / TMP / run_id
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def publish(storage_dir: Path, run_id: str) -> Path:
    staging = exports_dir(storage_dir) / TMP / run_id
    final = exports_dir(storage_dir) / run_id
    shutil.rmtree(final, ignore_errors=True)
    staging.replace(final)
    return final


def discard(storage_dir: Path, run_id: str) -> None:
    shutil.rmtree(exports_dir(storage_dir) / TMP / run_id, ignore_errors=True)


def artifact_path(storage_dir: Path, run_id: str, name: str) -> Path:
    if name not in ARTIFACT_NAMES:
        raise InvalidArtifactNameError("Unknown export artifact.", name=name[:64])
    path = exports_dir(storage_dir) / run_id / name
    if not path.exists():
        raise InvalidArtifactNameError("That artifact was not produced by this export.", name=name)
    return path
