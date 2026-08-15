"""Readiness must describe scholarly coverage, not merely process health."""

from __future__ import annotations

from typing import Any

from fastapi import Response

from app.main import ready
from app.settings import get_settings


def test_readiness_requires_both_scholarly_keys_for_full_coverage(monkeypatch: Any) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "openalex_api_key", "openalex-key")
    monkeypatch.setattr(settings, "semantic_scholar_api_key", "")
    monkeypatch.setattr("app.main.check_database", lambda: True)
    monkeypatch.setattr("app.main.GrobidClient.is_alive", lambda _self: True)
    monkeypatch.setattr("app.main.shutil.which", lambda _name: "/usr/bin/pandoc")

    payload = ready(Response())

    assert payload["scholarly_search_keyed"] is False
    assert payload["openalex_keyed"] is True
    assert payload["semantic_scholar_keyed"] is False
    assert payload["warnings"] == [
        "Semantic Scholar has no API key. Its unauthenticated shared quota is not "
        "reliable enough for complete review coverage."
    ]


def test_readiness_reports_full_coverage_only_when_both_keys_exist(monkeypatch: Any) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "openalex_api_key", "openalex-key")
    monkeypatch.setattr(settings, "semantic_scholar_api_key", "s2-key")
    monkeypatch.setattr("app.main.check_database", lambda: True)
    monkeypatch.setattr("app.main.GrobidClient.is_alive", lambda _self: True)
    monkeypatch.setattr("app.main.shutil.which", lambda _name: "/usr/bin/pandoc")

    payload = ready(Response())

    assert payload["scholarly_search_keyed"] is True
    assert payload["warnings"] == []
