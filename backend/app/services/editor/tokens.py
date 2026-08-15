from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.errors import ProtectedTokenError
from app.services.parser.segmenter import TOKEN_PATTERN, token_ids


@dataclass(frozen=True)
class TokenAudit:
    before: tuple[str, ...]
    after: tuple[str, ...]

    @property
    def kept(self) -> tuple[str, ...]:
        return tuple(token for token in self.before if token in set(self.after))

    @property
    def dropped(self) -> tuple[str, ...]:
        return tuple(token for token in self.before if token not in set(self.after))

    @property
    def reordered(self) -> bool:
        return self.kept != tuple(token for token in self.after if token in set(self.before))


def audit(before_text: str, after_text: str, paragraph_id: str) -> TokenAudit:
    before = token_ids(before_text)
    after = token_ids(after_text)

    duplicated = _duplicates(after)
    if duplicated:
        raise ProtectedTokenError(
            "The rewrite repeated a citation token, which would assert one citation twice.",
            paragraph_id=paragraph_id,
            token_ids=sorted(duplicated),
        )

    invented = [token for token in after if token not in set(before)]
    if invented:
        raise ProtectedTokenError(
            "The rewrite introduced a citation token that was not in the paragraph.",
            paragraph_id=paragraph_id,
            token_ids=sorted(set(invented)),
        )

    malformed = _malformed(after_text, after)
    if malformed:
        raise ProtectedTokenError(
            "The rewrite altered the text of a citation token.",
            paragraph_id=paragraph_id,
            detail=malformed,
        )

    return TokenAudit(before=before, after=after)


def _duplicates(tokens: tuple[str, ...]) -> set[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for token in tokens:
        if token in seen:
            repeated.add(token)
        seen.add(token)
    return repeated


def _malformed(text: str, parsed: tuple[str, ...]) -> str:
    opened = text.count("[[CITE:")
    if opened != len(parsed):
        return f"{opened} token openings but {len(parsed)} well-formed tokens"
    return ""


def rehydrate(tokenised: str, allowed: frozenset[str]) -> str:

    def replace(match: re.Match[str]) -> str:
        identifier = match.group(1)
        return match.group(0) if identifier in allowed else ""

    return TOKEN_PATTERN.sub(replace, tokenised)
