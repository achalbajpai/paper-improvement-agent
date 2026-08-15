"""Grounding: the model may only return identifiers the server issued to it.

Every LLM call in this system is shaped the same way. The server builds a list of
identifiers -- sentence ids, span ids, candidate ids -- puts them in the prompt,
and accepts back nothing but members of that list. The model never returns text,
so there is no paraphrase to match, no normalisation to get wrong, and no way for
a fabricated title or a hallucinated quotation to reach the manuscript: the
server looks up its own text by id and does the quoting itself.

An unknown identifier is not a warning and not a dropped item. It means the model
answered about something that does not exist, which invalidates the whole
response, so it raises. ``GroundingValidationError`` rather than ``assert``:
``python -O`` strips assertions, and the check that stops an invented citation
from reaching a researcher's paper must not be removable by an interpreter flag.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TypeVar

from app.domain.errors import GroundingValidationError

V = TypeVar("V")


class Allowlist[T]:
    """Identifiers offered to a model, and the objects they stand for."""

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
        """Look up one returned identifier, or raise."""
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
        """Resolve every identifier, or raise on the first unknown one.

        All-or-nothing on purpose. Silently discarding the unknown ids would let
        a response that was half invented through, labelled as if it were sound.
        """
        return [self.resolve(identifier, prompt=prompt) for identifier in identifiers]


def require_choice(value: str, allowed: Iterable[str], *, prompt: str, field: str) -> str:
    """Constrain a returned enum-like string to the values the prompt permitted.

    Strict structured output already constrains the schema, but the verdict set
    is a safety boundary -- ``UNSUPPORTED`` is deliberately absent from it -- and
    a boundary that depends on a provider honouring ``strict: true`` is a
    boundary held by somebody else's release notes.
    """
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
