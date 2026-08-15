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
    def __init__(self) -> None:
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def key(provider: str, operation: str, argument: str, *parts: str) -> str:
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
