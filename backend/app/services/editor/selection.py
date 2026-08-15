from __future__ import annotations

from pydantic import BaseModel, Field

from app.providers.llm import Prompt, delimit

PROMPT_VERSION = "selection/v1"


class Selection(BaseModel):
    target_sentence_id: str = Field(description="A sentence id from the supplied list, verbatim.")
    candidate_id: str = Field(description="A candidate id from the supplied list, verbatim.")
    rationale: str = Field(
        max_length=300,
        description="Why this work supports that specific sentence.",
    )


class SelectionResponse(BaseModel):
    selections: list[Selection] = Field(
        default_factory=list,
        max_length=10,
        description="Leave empty when no candidate genuinely supports any sentence.",
    )


SYSTEM = (
    "You attach retrieved scholarly works to the specific sentences they support. "
    "Use only sentence ids and candidate ids from the supplied lists, verbatim. "
    "Attach a work only where it genuinely supports that sentence's claim; "
    "returning nothing is correct when nothing fits. Never rewrite the sentence, "
    "and never invent a work or an identifier."
)


def build_prompt(
    sentences: list[tuple[str, str]],
    candidates: list[tuple[str, str]],
    limit: int,
) -> Prompt:
    sentence_listing = "\n\n".join(f"{sentence_id}: {text}" for sentence_id, text in sentences)
    candidate_listing = "\n\n".join(
        f"{candidate_id}: {summary}" for candidate_id, summary in candidates
    )
    user = (
        f"{delimit('Uncited sentences', sentence_listing)}\n\n"
        f"{delimit('Retrieved candidates', candidate_listing)}\n\n"
        f"Attach at most {limit} works, at most one per sentence. "
        "A citation will be inserted into the sentence exactly as written; "
        "the sentence itself will not be changed."
    )
    return Prompt(name="selection", version=PROMPT_VERSION, system=SYSTEM, user=user)
