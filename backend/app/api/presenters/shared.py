"""Values every presenter needs, and nothing a single resource owns."""

from __future__ import annotations

from app.domain.errors import ErrorCode
from app.domain.reference import CSLItem, ReferenceRecord


def _error_code(value: str | None) -> ErrorCode | None:
    return ErrorCode(value) if value else None


def _author_names(csl: CSLItem | None) -> list[str]:
    if csl is None:
        return []
    names: list[str] = []
    for author in csl.author:
        literal = str(author.get("literal") or "").strip()
        family = str(author.get("family") or "").strip()
        given = str(author.get("given") or "").strip()
        name = literal or " ".join(part for part in (given, family) if part)
        if name:
            names.append(name)
    return names


def _https_only(url: str | None) -> str | None:
    """Provider-supplied links are untrusted input; only https reaches the UI."""
    return url if url and url.startswith("https://") else None


def _reference_label(reference: ReferenceRecord | None) -> str | None:
    if reference is None:
        return None
    if reference.csl.title:
        return reference.csl.title
    return reference.raw_text[:160] or None
