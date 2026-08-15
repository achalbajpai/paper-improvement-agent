"""Paragraph rewriting: one LLM call, one schema, one versioned prompt.

Scope is one paragraph in, one paragraph out. There is no unconstrained section
rewrite and no implicit merge or split, because a model that can restructure a
section can silently drop a paragraph, and paragraph identity is what every
anchor, finding, and delta depends on.

Citations reach the model only as protected tokens (``[[CITE:cite_012]]``),
never as the mutable rendered label ``[12]``. Preserved blocks are never
serialised into a prompt at all.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.providers.llm import Prompt, delimit

PROMPT_VERSION = "rewrite/v1"


class RewriteResponse(BaseModel):
    revised_text: str = Field(
        description=(
            "The shortened paragraph. Every [[CITE:...]] token that is kept must be "
            "reproduced exactly. No token may be invented, duplicated, or altered."
        )
    )
    claimed_actions: list[str] = Field(
        description=(
            "What the model believes it did. Stored for debugging and never "
            "authoritative: the DeltaEngine computes what actually changed."
        )
    )


SYSTEM = (
    "You shorten a single paragraph of a research paper. Preserve meaning exactly: "
    "never add a fact, a number, an entity, or a causal claim that the original "
    "does not state, and never strengthen a hedged statement. Tokens of the form "
    "[[CITE:xxx]] are citations. Reproduce every token you keep character for "
    "character. Never invent a new token and never write the same token twice. "
    "Return the shortened paragraph only."
)


def build_prompt(tokenised_paragraph: str, target_words: int) -> Prompt:
    user = (
        f"{delimit('Paragraph, with citations as protected tokens', tokenised_paragraph)}\n\n"
        f"Shorten this paragraph to roughly {target_words} words while preserving "
        "its meaning and its citation tokens."
    )
    return Prompt(name="rewrite", version=PROMPT_VERSION, system=SYSTEM, user=user)
