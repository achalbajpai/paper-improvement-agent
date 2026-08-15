"""Protected tokens: what a rewriting model is allowed to do to a citation.

A citation reaches the model as ``[[CITE:cite_imported_012]]`` and never as the
rendered label ``[12]``. The rendered form is style-dependent and mutable, so a
model that "kept the citations" while renumbering them would have silently
reattributed the author's claims. The token is opaque, carries no meaning to
rewrite, and is checked by identity.

The matrix below is the whole policy. Every outcome is either allowed or a typed
error; there is no case where an unexpected token difference is tolerated because
it looked harmless:

    kept in place    allowed   the ordinary case
    dropped          allowed   the point of shortening, and recorded as a removal
    reordered        allowed   sentences move when a paragraph is compressed
    duplicated       ERROR     one occurrence became two assertions of support
    invented         ERROR     a citation that was never in the manuscript
    mutated          ERROR     a token altered by a character is a different work

Duplication is an error rather than a silent de-duplication because the model
believed it was citing something twice and this system does not get to guess
which occurrence it meant.

Removal is *permitted here* and *policed later*: the DeltaEngine records it and
verification decides whether the researcher must acknowledge it. This module's
only job is deciding whether the returned text is structurally admissible.

Pure: no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.errors import ProtectedTokenError
from app.services.parser.segmenter import TOKEN_PATTERN, token_ids


@dataclass(frozen=True)
class TokenAudit:
    """What happened to the citation tokens of one paragraph."""

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
    """Check a rewritten paragraph's tokens, or raise.

    ``before_text`` and ``after_text`` are both tokenised forms, so this compares
    citation identity rather than rendered labels.
    """
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
    """Catch a token the pattern could not read.

    ``[[CITE:cite_012`` with the closing brackets lost parses as no token at all,
    so counting well-formed tokens would report it as a clean removal. Comparing
    the count of opening delimiters against the count of parsed tokens catches
    the mangling instead.
    """
    opened = text.count("[[CITE:")
    if opened != len(parsed):
        return f"{opened} token openings but {len(parsed)} well-formed tokens"
    return ""


def rehydrate(tokenised: str, allowed: frozenset[str]) -> str:
    """Strip any token outside the allowed set. A last line, not the first.

    Nothing should reach this with an unknown token, because ``audit`` raises
    first. It exists so that a future call path which forgets to audit degrades
    to dropping an unknown token rather than to emitting one.
    """

    def replace(match: re.Match[str]) -> str:
        identifier = match.group(1)
        return match.group(0) if identifier in allowed else ""

    return TOKEN_PATTERN.sub(replace, tokenised)
