from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TypeVar

from app.domain.errors import GroundingValidationError

V = TypeVar("V")


class Allowlist[T]:
    def __init__(self, kind: str, entries: Mapping[str, T]) -> None:
        self.kind = kind
        self._entries = dict(entries)

    def __contains__(self, identifier: object) -> bool:
        return identifier in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(self._entries)

    def resolve(self, identifier: str, *, prompt: str) -> T:
        if identifier not in self._entries:
            raise GroundingValidationError(
                "The model returned an identifier that was never offered to it.",
                prompt=prompt,
                kind=self.kind,
                returned_id=identifier[:64],
                offered_count=len(self._entries),
            )
        return self._entries[identifier]

    def resolve_all(self, identifiers: Iterable[str], *, prompt: str) -> list[T]:
        return [self.resolve(identifier, prompt=prompt) for identifier in identifiers]


def require_choice(value: str, allowed: Iterable[str], *, prompt: str, field: str) -> str:
    permitted = tuple(allowed)
    if value not in permitted:
        raise GroundingValidationError(
            "The model returned a value outside the permitted set.",
            prompt=prompt,
            field=field,
            returned=value[:64],
            permitted=list(permitted),
        )
    return value
