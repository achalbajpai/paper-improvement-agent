"""Operation deadlines.

An operation gets one budget, and every call inside it receives the *remaining*
time rather than a fresh timeout of its own. Without that, four calls with a
ten-second timeout each produce a forty-second operation on a request the caller
believed had a ten-second ceiling, and the failure looks like a hang.

Uses the monotonic clock: a system clock adjustment mid-operation must not turn a
deadline into the past or the distant future.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.domain.errors import OperationTimedOutError


@dataclass(frozen=True)
class Deadline:
    operation: str
    expires_at: float

    @classmethod
    def after(cls, operation: str, seconds: float) -> Deadline:
        return cls(operation=operation, expires_at=time.monotonic() + seconds)

    def remaining(self) -> float:
        return max(0.0, self.expires_at - time.monotonic())

    @property
    def expired(self) -> bool:
        return self.remaining() <= 0.0

    def check(self, step: str = "") -> None:
        """Raise if the budget is gone. Called before starting each slow step."""
        if self.expired:
            raise OperationTimedOutError(
                "The operation exceeded its time budget.",
                operation=self.operation,
                step=step,
            )

    def budget_for(self, seconds: float) -> float:
        """A per-call timeout that cannot outlive the operation."""
        return min(seconds, self.remaining())
