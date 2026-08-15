from __future__ import annotations

from pydantic import BaseModel, Field

from app.providers.llm import Prompt, delimit

PROMPT_VERSION = "novelty/v1"


class NoveltyFinding(BaseModel):
    assertion: str = Field(
        max_length=300, description="The unentailed assertion, quoted from the revised text."
    )
    kind: str = Field(
        description=(
            "One of: NEW_NUMBER, REMOVED_QUALIFIER, STRENGTHENED_CAUSALITY, "
            "WIDENED_POPULATION, STRENGTHENED_MODALITY, NEW_ENTITY, NEW_COMPARISON, OTHER."
        )
    )


class NoveltyResponse(BaseModel):
    has_unsupported_novelty: bool = Field(
        description="True when the revised text asserts anything the original did not."
    )
    findings: list[NoveltyFinding] = Field(max_length=8)


SYSTEM = (
    "You compare an original paragraph with a shortened revision and report any "
    "assertion in the revision that is not entailed by the original. Treat these "
    "as unsupported novelty: a new number or statistic, a removed hedge or "
    "qualifier, an association restated as causation, a widened population or "
    "scope, strengthened modality, a new named entity, and a new comparison. "
    "Removing information is not novelty. Rewording that preserves meaning is not "
    "novelty."
)


def build_prompt(before: str, after: str) -> Prompt:
    user = (
        f"{delimit('Original paragraph', before)}\n\n"
        f"{delimit('Shortened revision', after)}\n\n"
        "Report every assertion in the revision that the original does not entail."
    )
    return Prompt(name="novelty", version=PROMPT_VERSION, system=SYSTEM, user=user)
