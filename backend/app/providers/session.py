"""One operation's provider state: budget, degradation, and the attempt log.

Degradation is **request-scoped and sticky**. Unauthenticated Semantic Scholar
can return 429 routinely, so a rate limit is ordinary behaviour rather than an
incident. Once a provider has been rate limited
inside an operation, this session stops calling it for the rest of that
operation: retrying twenty more times converts one slow request into a very slow
one and gets the caller no further.

Every degradation is recorded and surfaced. A review that quietly skipped a
provider is a review whose coverage the researcher cannot judge, and
``PROVIDER_RATE_LIMITED`` being visibly distinct from ``NO_RESULTS`` is the whole
point: "we could not look" and "we looked and there is nothing" are different
answers to the researcher's question.
"""

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
    """One provider call, for the ``retrieval_attempts`` table.

    ``query`` is manuscript-derived, so it is carried here to be written to a
    paper-scoped row and never to a log line.
    """

    provider: ProviderName
    purpose: RetrievalPurpose
    query: str
    status: str
    failure_code: str | None
    result_count: int
    latency_ms: int


@dataclass
class ProviderSession:
    """Per-operation provider state. Not shared between operations."""

    operation_id: str
    calls: dict[ProviderName, int] = field(default_factory=dict)
    attempts_made: dict[ProviderName, int] = field(default_factory=dict)
    """Requests the transport actually sent, which a retried call makes exceed
    the number reserved."""
    _degraded: dict[ProviderName, ProviderDegradation] = field(default_factory=dict)
    attempts: list[AttemptRecord] = field(default_factory=list)
    _lock: RLock = field(default_factory=RLock, repr=False)

    def degrade(self, provider: ProviderName, error: ProviderError) -> None:
        """Record a provider as unusable for the rest of this operation.

        The first degradation wins. A provider that rate-limited and then timed
        out is still, from the researcher's point of view, the provider that
        stopped answering.
        """
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
        """Reserve one call against this operation's budget.

        Raises rather than returning False: a caller that forgot to check a
        boolean would silently exceed the budget, and the budget exists to stop
        one manuscript from making four hundred provider calls.
        """
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
        """A hook the transport fires once per outbound request.

        ``claim_call`` reserves the operation's permission to make a call; this
        records what the call actually cost. A rate-limited request the
        transport retries is two requests, so counting only reservations makes
        both the budget and the reported ``provider_calls`` understate what was
        sent.
        """

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
        """Requests actually sent, which is never fewer than those reserved."""
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
    """Re-raise a degraded provider under the reason it was degraded for.

    Exhausting our own call budget and a provider refusing us are different
    facts, and only one of them is about the provider. Reporting both as a rate
    limit turns "this bounded operation stopped searching" into "the provider
    was unavailable" -- which misdescribes our own limit as somebody else's
    outage, in exactly the coverage note a researcher reads to judge the run.
    """
    if code == ErrorCode.PROVIDER_BUDGET_EXHAUSTED.value:
        return ProviderBudgetExhaustedError
    return ProviderRateLimitedError
