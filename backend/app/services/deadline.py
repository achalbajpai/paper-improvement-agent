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
        if self.expired:
            raise OperationTimedOutError(
                "The operation exceeded its time budget.",
                operation=self.operation,
                step=step,
            )

    def budget_for(self, seconds: float) -> float:
        return min(seconds, self.remaining())
