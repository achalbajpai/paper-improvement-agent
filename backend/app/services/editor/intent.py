"""Intent routing: one LLM call, one schema, one versioned prompt.

The router returns a structured intent plus target identifiers, all of which are
validated server-side against the actual document. An unsupported or ambiguous
intent never mutates the AST.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.providers.llm import Prompt, delimit

PROMPT_VERSION = "intent/v1"


class EditIntent(StrEnum):
    SHORTEN_SECTION = "SHORTEN_SECTION"
    ADD_SUPPORTING_CITATIONS = "ADD_SUPPORTING_CITATIONS"
    UNSUPPORTED_INTENT = "UNSUPPORTED_INTENT"
    AMBIGUOUS_INTENT = "AMBIGUOUS_INTENT"


class IntentResponse(BaseModel):
    intent: str = Field(description="One of the four intent strings.")
    section_id: str | None = Field(
        default=None, description="A section id from the supplied outline, or null."
    )
    paragraph_id: str | None = Field(
        default=None, description="A paragraph id from the supplied outline, or null."
    )
    target_reduction_ratio: float | None = Field(
        default=None, ge=0.1, le=0.9, description="For SHORTEN_SECTION only."
    )
    clarification: str | None = Field(
        default=None,
        max_length=300,
        description="For AMBIGUOUS_INTENT, what the researcher must choose between.",
    )


SYSTEM = (
    "You route a researcher's natural-language editing command to exactly one "
    "supported intent. Supported intents are SHORTEN_SECTION and "
    "ADD_SUPPORTING_CITATIONS. Use UNSUPPORTED_INTENT when the command asks for "
    "anything else. Use AMBIGUOUS_INTENT when the command is supported but does "
    "not identify which section or paragraph it applies to. Return only section "
    "and paragraph ids that appear in the supplied outline."
)


def build_prompt(command: str, outline: list[tuple[str, str]], target: str | None = None) -> Prompt:
    """Route one command, optionally against a target the researcher already chose.

    When ``target`` is present the researcher has pointed at a part of the paper,
    so the command cannot be ambiguous about where it applies -- only about what
    it asks for. Saying so in the prompt is what stops the model from returning
    AMBIGUOUS_INTENT for "shorten it by 20%" and discarding the intent along with
    the ambiguity it was never asked to resolve.
    """
    listing = "\n".join(f"{section_id}: {title}" for section_id, title in outline)
    selected = (
        f"{delimit('Already selected by the researcher', target)}\n\n"
        "The command applies to that selection, so it is not ambiguous about "
        "where it acts. Classify what it asks for.\n\n"
        if target
        else ""
    )
    user = (
        f"{delimit('Researcher command', command)}\n\n"
        f"{delimit('Document outline', listing)}\n\n"
        f"{selected}"
        "Route this command."
    )
    return Prompt(name="intent", version=PROMPT_VERSION, system=SYSTEM, user=user)
