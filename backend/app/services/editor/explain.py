"""Post-hoc explanation: one LLM call, one schema, one versioned prompt.

The explanation is generated *from the computed delta*, after the mutation, and
is shown beside it rather than instead of it. It is never authoritative: if the
explanation and the DeltaEngine disagree, the DeltaEngine is right and the
explanation is a presentation defect.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.providers.llm import Prompt, delimit

PROMPT_VERSION = "explain/v1"


class ExplanationResponse(BaseModel):
    summary: str = Field(
        max_length=600,
        description="A plain-language description of what the computed delta says changed.",
    )


SYSTEM = (
    "You describe, in plain language, a set of already-computed changes to a "
    "research paper. Describe only what the supplied change record states. Never "
    "add a change that is not listed and never speculate about intent."
)


def build_prompt(delta_summary: str) -> Prompt:
    user = (
        f"{delimit('Computed change record', delta_summary)}\n\n"
        "Summarise these changes for the researcher who must approve them."
    )
    return Prompt(name="explain", version=PROMPT_VERSION, system=SYSTEM, user=user)
