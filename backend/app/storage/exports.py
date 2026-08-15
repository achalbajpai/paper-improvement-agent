"""Export artifacts on disk, published atomically.

Artifacts are built into ``exports/.tmp/{run_id}`` and moved into
``exports/{run_id}`` with a single rename once every file exists. A rename within
one filesystem is atomic, so a reader either sees a complete export directory or
sees nothing -- never a PDF that is still being written, which is exactly what a
researcher would download at the wrong moment otherwise.

Artifact names are validated against a fixed allowlist rather than sanitised. A
request never contributes a path component, so path traversal is not a bug class
this module can have.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.domain.errors import InvalidArtifactNameError
from app.services.exporter.renderer import ARTIFACT_NAMES
from app.storage.manuscripts import exports_dir

TMP = ".tmp"


def staging_dir(storage_dir: Path, run_id: str) -> Path:
    """A private directory to build one export in."""
    path = exports_dir(storage_dir) / TMP / run_id
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def publish(storage_dir: Path, run_id: str) -> Path:
    """Move a completed export into place with one rename."""
    staging = exports_dir(storage_dir) / TMP / run_id
    final = exports_dir(storage_dir) / run_id
    shutil.rmtree(final, ignore_errors=True)
    staging.replace(final)
    return final


def discard(storage_dir: Path, run_id: str) -> None:
    shutil.rmtree(exports_dir(storage_dir) / TMP / run_id, ignore_errors=True)


def artifact_path(storage_dir: Path, run_id: str, name: str) -> Path:
    """Resolve one artifact, refusing any name that is not on the allowlist.

    The allowlist is the whole check: a name that is not one of the four files an
    export produces is rejected before it ever reaches the filesystem.
    """
    if name not in ARTIFACT_NAMES:
        raise InvalidArtifactNameError("Unknown export artifact.", name=name[:64])
    path = exports_dir(storage_dir) / run_id / name
    if not path.exists():
        raise InvalidArtifactNameError("That artifact was not produced by this export.", name=name)
    return path
