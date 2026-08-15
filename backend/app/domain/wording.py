"""Small helpers for the sentences the server writes for people to read.

Warnings and blockers are shown to a researcher who is being asked to accept a
consequence. "1 figures are missing" reads as machine-generated, and a safety
notice that looks carelessly produced invites being clicked through -- which is
the one behaviour these messages exist to prevent.

Pure: no I/O, no state.
"""

from __future__ import annotations


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    """``1 figure`` or ``3 figures``, with an explicit form for irregular nouns.

    English pluralisation is not a rule this needs to learn: the caller knows the
    noun, and ``entry``/``entries`` is passed in rather than guessed at.
    """
    if count == 1:
        return f"1 {singular}"
    return f"{count} {plural_form or f'{singular}s'}"


def is_are(count: int) -> str:
    """Agreement for a sentence whose subject is one of the counts above."""
    return "is" if count == 1 else "are"
