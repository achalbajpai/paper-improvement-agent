from __future__ import annotations

from pydantic import BaseModel, Field

from app.providers.llm import Prompt, delimit

PROMPT_VERSION = "rerank/v1"


class RankedCandidate(BaseModel):
    candidate_id: str = Field(description="An id from the supplied candidate list, verbatim.")
    rank: int = Field(ge=1, description="1 is the most relevant.")
    rationale: str = Field(
        max_length=300,
        description="Why this work is relevant to the claim. Shown as interpretation.",
    )


class RerankResponse(BaseModel):
    ranked: list[RankedCandidate] = Field(max_length=10)


SYSTEM = (
    "You rank retrieved scholarly works by how well each one would support a "
    "specific claim. Return only candidate ids from the supplied list. Never "
    "invent a work, an author, a title, or an identifier."
)


def build_prompt(claim_text: str, candidates: list[tuple[str, str]], keep: int) -> Prompt:
    listing = "\n\n".join(f"{candidate_id}: {summary}" for candidate_id, summary in candidates)
    user = (
        f"{delimit('Claim from the manuscript', claim_text)}\n\n"
        f"{delimit('Retrieved candidates', listing)}\n\n"
        f"Rank at most {keep} candidates by relevance to the claim."
    )
    return Prompt(name="rerank", version=PROMPT_VERSION, system=SYSTEM, user=user)
