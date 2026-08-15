from __future__ import annotations


def plural(count: int, singular: str, plural_form: str | None = None) -> str:
    if count == 1:
        return f"1 {singular}"
    return f"{count} {plural_form or f'{singular}s'}"


def is_are(count: int) -> str:
    return "is" if count == 1 else "are"
