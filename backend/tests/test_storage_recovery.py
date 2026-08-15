from __future__ import annotations

import os
import time
import uuid

from app.settings import get_settings
from app.storage.manuscripts import quarantine_orphaned_papers, sweep_trash


def test_only_unreferenced_server_generated_directories_are_quarantined(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    known = str(uuid.uuid4())
    orphan = str(uuid.uuid4())
    human = "manual-backup"
    for name in (known, orphan, human):
        (get_settings().papers_dir / name).mkdir(parents=True)

    assert quarantine_orphaned_papers({known}) == 1
    assert (get_settings().papers_dir / known).is_dir()
    assert (get_settings().papers_dir / human).is_dir()
    assert not (get_settings().papers_dir / orphan).exists()
    assert any(path.name.startswith(orphan) for path in get_settings().trash_dir.iterdir())


def test_symlinks_are_never_followed_or_quarantined(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "data_dir", tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    link = get_settings().papers_dir / str(uuid.uuid4())
    link.parent.mkdir(parents=True)
    link.symlink_to(outside, target_is_directory=True)

    assert quarantine_orphaned_papers(set()) == 0
    assert link.is_symlink()
    assert outside.is_dir()


def test_trash_is_retained_for_recovery_before_it_is_swept(tmp_path, monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "trash_retention_seconds", 3600)
    recent = settings.trash_dir / "recent"
    old = settings.trash_dir / "old"
    recent.mkdir(parents=True)
    old.mkdir()
    old_time = time.time() - 7200
    os.utime(old, (old_time, old_time))

    assert sweep_trash() == 1
    assert recent.is_dir()
    assert not old.exists()
