from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

import httpx

from app.domain.errors import (
    ProviderInvalidResponseError,
    ProviderNotFoundError,
    ProviderQueryRejectedError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from app.settings import get_settings

USER_AGENT = "answerthis-paper-agent/0.1 (+https://github.com/answerthis)"


@dataclass
class RequestPacer:
    _next_request_at: dict[str, float] = field(default_factory=dict)
    _quota_locks: dict[str, Lock] = field(default_factory=dict)
    _registry_lock: Lock = field(default_factory=Lock)

    def acquire(self, quota: str, *, min_interval: float, timeout: float) -> float:
        if min_interval <= 0:
            return timeout
        with self._quota_lock(quota):
            now = time.monotonic()
            wait = max(0.0, self._next_request_at.get(quota, now) - now)
            if wait >= timeout:
                raise ProviderRateLimitedError(
                    "The request could not wait for a provider quota slot within its deadline.",
                    provider=quota,
                    retry_after_seconds=wait,
                )
            if wait:
                time.sleep(wait)
            sent_at = time.monotonic()
            self._next_request_at[quota] = sent_at + min_interval
            return max(0.001, timeout - wait)

    def _quota_lock(self, quota: str) -> Lock:
        with self._registry_lock:
            return self._quota_locks.setdefault(quota, Lock())


_PACER = RequestPacer()


def request_json(
    method: str,
    url: str,
    *,
    provider: str,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float,
    retry_on_rate_limit: bool = False,
    min_interval_seconds: float = 0.0,
    on_attempt: Callable[[], None] | None = None,
) -> dict[str, Any]:
    attempts = 2 if retry_on_rate_limit else 1
    last_rate_limit: ProviderRateLimitedError | None = None
    deadline = time.monotonic() + timeout

    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise last_rate_limit or ProviderUnavailableError(
                "The provider request exhausted its deadline before an HTTP attempt.",
                provider=provider,
                timeout_seconds=timeout,
            )
        remaining = _PACER.acquire(provider, min_interval=min_interval_seconds, timeout=remaining)
        if on_attempt is not None:
            on_attempt()
        response = _send(
            method, url, provider=provider, params=params, headers=headers, timeout=remaining
        )

        if response.status_code == 429:
            last_rate_limit = ProviderRateLimitedError(
                "The provider rate limited this request.",
                provider=provider,
                retry_after_seconds=_retry_after(response),
            )
            if attempt + 1 < attempts:
                wait = _retry_after(response)
                if wait >= deadline - time.monotonic():
                    raise last_rate_limit
                time.sleep(wait)
                continue
            raise last_rate_limit

        if response.status_code == 404:
            raise ProviderNotFoundError("The provider has no record here.", provider=provider)
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                "The provider returned a server error.",
                provider=provider,
                status_code=response.status_code,
            )
        if response.status_code in {400, 422}:
            raise ProviderQueryRejectedError(
                "The provider rejected this request as malformed.",
                provider=provider,
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise ProviderInvalidResponseError(
                "The provider rejected this request.",
                provider=provider,
                status_code=response.status_code,
            )

        return _decode(response, provider)

    raise last_rate_limit or ProviderUnavailableError("No response.", provider=provider)


def _send(
    method: str,
    url: str,
    *,
    provider: str,
    params: dict[str, Any] | None,
    headers: dict[str, str] | None,
    timeout: float,
) -> httpx.Response:
    merged = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    merged.update(headers or {})
    try:
        return httpx.request(method, url, params=params, headers=merged, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise ProviderUnavailableError(
            "The provider did not respond within the remaining budget.",
            provider=provider,
            timeout_seconds=timeout,
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError("The provider is not reachable.", provider=provider) from exc


def _decode(response: httpx.Response, provider: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderInvalidResponseError(
            "The provider returned a body that is not JSON.",
            provider=provider,
            content_type=response.headers.get("content-type", ""),
        ) from exc
    if not isinstance(payload, dict):
        raise ProviderInvalidResponseError(
            "The provider returned a JSON value that is not an object.", provider=provider
        )
    return payload


def _retry_after(response: httpx.Response) -> float:
    cap = get_settings().s2_max_retry_after_seconds
    raw = response.headers.get("Retry-After", "")
    try:
        requested = float(raw)
    except ValueError:
        return cap
    return max(0.0, min(requested, cap))
