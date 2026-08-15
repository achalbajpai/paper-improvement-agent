"""Canonical hashing.

Snapshot identity, warning identity, and acceptance all compare hashes. Python's
built-in ``hash()`` is salted per process (PYTHONHASHSEED), so a value hashed in
one worker will not match the same value hashed in another; using it here would
produce a system that appears to work until it is restarted. Everything hashes
through ``canonical_sha256``.

Canonicalisation is three rules, applied in this order:

1. Unicode NFC, so a composed and a decomposed accent are the same string.
2. Insignificant whitespace collapsed, so a reflowed paragraph that reads
   identically hashes identically.
3. Object keys sorted and separators fixed, so key ordering never leaks in.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any

from pydantic import BaseModel


def normalize_text(text: str) -> str:
    """NFC, with runs of whitespace collapsed to single spaces and ends trimmed."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def canonical_json(value: Any) -> str:
    return json.dumps(
        _canonicalize(value),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _canonicalize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _canonicalize(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    return value


def canonical_sha256(value: Any) -> str:
    """The only hash function in this system."""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def text_sha256(text: str) -> str:
    """Hash of prose, whitespace-insensitive.

    Used for anchors: a paragraph that was reflowed but not reworded must keep
    its anchors, while a paragraph whose wording changed must lose them.
    """
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()
