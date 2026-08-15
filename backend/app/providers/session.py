from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from threading import RLock

from app.domain.errors import (
    ErrorCode,
    ProviderBudgetExhaustedError,
    ProviderError,
    ProviderRateLimitedError,
)
from app.domain.review import ProviderDegradation
from app.domain.source import ProviderName, RetrievalPurpose
from app.observability import get_logger
from app.settings import get_settings

logger = get_logger(__name__)


@dataclass(frozen=True)
class AttemptRecord:
    provider: ProviderName
    purpose: RetrievalPurpose
    query: str
    status: str
    failure_code: str | None
    result_count: int
    latency_ms: int


@dataclass
class ProviderSession:
    operation_id: str
    calls: dict[ProviderName, int] = field(default_factory=dict)
    attempts_made: dict[ProviderName, int] = field(default_factory=dict)
    """Requests the transport actually sent, which a retried call makes exceed
    the number reserved."""
    _degraded: dict[ProviderName, ProviderDegradation] = field(default_factory=dict)
    attempts: list[AttemptRecord] = field(default_factory=list)
    _lock: RLock = field(default_factory=RLock, repr=False)

    def degrade(self, provider: ProviderName, error: ProviderError) -> None:
        if provider in self._degraded:
            return
        self._degraded[provider] = ProviderDegradation(
            provider=provider.value, code=error.code.value, detail=error.message
        )
        logger.warning(
            "provider.degraded",
            extra={
                "operation_id": self.operation_id,
                "provider": provider.value,
                "code": error.code.value,
            },
        )

    def is_degraded(self, provider: ProviderName) -> bool:
        return provider in self._degraded

    @property
    def degradations(self) -> tuple[ProviderDegradation, ...]:
        return tuple(self._degraded.values())

    def degradation_for(self, provider: ProviderName) -> ProviderDegradation | None:
        return self._degraded.get(provider)

    def claim_call(self, provider: ProviderName) -> None:
        with self._lock:
            if self.is_degraded(provider):
                degradation = self._degraded[provider]
                raise _degradation_error(degradation.code)(
                    "This provider is unavailable for the rest of this operation.",
                    provider=provider.value,
                    code=degradation.code,
                )

            settings = get_settings()
            used = self.calls.get(provider, 0)
            limit = self._limit(provider)
            total = sum(self.calls.values())
            if used >= limit or total >= settings.max_provider_calls_per_operation:
                raise ProviderBudgetExhaustedError(
                    "This operation has used its provider call budget.",
                    provider=provider.value,
                    used=used,
                    limit=limit,
                )
            self.calls[provider] = used + 1

    def record(self, attempt: AttemptRecord) -> None:
        self.attempts.append(attempt)

    def attempt_recorder(self, provider: ProviderName) -> Callable[[], None]:

        def record() -> None:
            with self._lock:
                settings = get_settings()
                used = self.attempts_made.get(provider, 0)
                limit = self._limit(provider)
                total = sum(self.attempts_made.values())
                if used >= limit or total >= settings.max_provider_calls_per_operation:
                    raise ProviderBudgetExhaustedError(
                        "This operation has used its provider HTTP-attempt budget.",
                        provider=provider.value,
                        used=used,
                        limit=limit,
                    )
                self.attempts_made[provider] = used + 1

        return record

    @property
    def total_calls(self) -> int:
        return max(sum(self.calls.values()), sum(self.attempts_made.values()))

    @staticmethod
    def _limit(provider: ProviderName) -> int:
        settings = get_settings()
        return (
            settings.max_openalex_calls_per_operation
            if provider is ProviderName.OPENALEX
            else settings.max_provider_calls_per_operation
        )


def _degradation_error(code: str) -> type[ProviderError]:
    if code == ErrorCode.PROVIDER_BUDGET_EXHAUSTED.value:
        return ProviderBudgetExhaustedError
    return ProviderRateLimitedError
