from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from app.providers.llm import Prompt, delimit
from app.services.parser.segmenter import Sentence

PROMPT_VERSION = "claims/v1"


@dataclass(frozen=True)
class ClaimTarget:
    sentence: Sentence
    topic: str


class ExtractedClaim(BaseModel):
    sentence_id: str = Field(description="An id from the supplied sentence list, verbatim.")
    is_citable_claim: bool = Field(
        description="True when the sentence asserts something a source could support."
    )
    topic: str = Field(
        max_length=160,
        description="A short topical description used to build a search query.",
    )


class ClaimExtractionResponse(BaseModel):
    claims: list[ExtractedClaim] = Field(max_length=8)


SYSTEM = (
    "You identify which sentences in a paragraph make factual or scholarly claims "
    "that a citation could support. Return only sentence ids that appear in the "
    "supplied list. Do not return sentence text."
)


def build_prompt(paragraph_sentences: list[tuple[str, str]], max_claims: int) -> Prompt:
    listing = "\n".join(f"{sentence_id}: {text}" for sentence_id, text in paragraph_sentences)
    user = (
        f"{delimit('Paragraph sentences', listing)}\n\n"
        f"Select at most {max_claims} sentences that make a citable claim. "
        "Return the sentence id, whether it is a citable claim, and a short topic "
        "phrase suitable for a literature search."
    )
    return Prompt(name="claims", version=PROMPT_VERSION, system=SYSTEM, user=user)
