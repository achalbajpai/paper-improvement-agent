"""Provider response cache.

One rule decides everything here: **a transient failure is never a cached
result.** A 429 or a 502 says nothing about the work being looked up, and storing
it would convert a momentary rate limit into hours of confidently reported
absence -- the precise confusion between "we could not look" and "there is
nothing there" that this system exists to avoid.

A genuine empty result *is* a fact about the query and is cached, on a shorter
lifetime than a hit, because a paper that did not exist this morning may exist
this afternoon.

The store is process-local and bounded. Nothing here is a source of truth:
anything that must survive a restart is snapshotted into ``source_records``.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.settings import get_settings

_MAX_ENTRIES = 2048


@dataclass(frozen=True)
class _Entry:
    value: Any
    expires_at: float


class ProviderCache:
    """A small TTL cache keyed by ``(provider, operation, argument)``."""

    def __init__(self) -> None:
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(provider: str, operation: str, argument: str, *parts: str) -> str:
        """Every input that changes the response belongs in the key.

        A search's result count does: without it, a request for ten results can
        be served the three a narrower earlier call stored, and the shortfall
        reads as the provider having nothing more to offer.
        """
        suffix = "".join(f"::{part}" for part in parts)
        return f"{provider}::{operation}::{argument.casefold()}{suffix}"

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            return entry.value

    def put(self, key: str, value: Any, *, empty: bool = False) -> None:
        """Store a result.

        ``empty`` marks a genuine negative -- the provider answered and had
        nothing -- which gets the shorter lifetime. Callers must not route
        failures through here at all; there is deliberately no way to express
        one.
        """
        settings = get_settings()
        ttl = settings.cache_ttl_no_results_seconds if empty else settings.cache_ttl_success_seconds
        with self._lock:
            self._entries[key] = _Entry(value=value, expires_at=time.monotonic() + ttl)
            self._entries.move_to_end(key)
            while len(self._entries) > _MAX_ENTRIES:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


_cache = ProviderCache()


def get_cache() -> ProviderCache:
    return _cache
