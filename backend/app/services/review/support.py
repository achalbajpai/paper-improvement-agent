"""Claim-citation support: one LLM call, one schema, one versioned prompt.

The verdict set deliberately omits ``UNSUPPORTED``. Failing to find support in an
abstract is not evidence that the cited paper lacks it -- the material may be in
the full text -- and reporting it as unsupported would launder absence of
evidence into a finding against the researcher's citation. Every verdict is
labelled an abstract-level assessment.

The model returns a verdict and evidence *span ids*. The server validates each
id and constructs the EvidenceAnchor, so there is no paraphrase matching and no
normalisation ambiguity.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.review import MODEL_SELECTABLE_VERDICTS, SupportVerdict
from app.providers.llm import Prompt, delimit

PROMPT_VERSION = "support/v1"

__all__ = ["MODEL_SELECTABLE_VERDICTS", "SupportResponse", "SupportVerdict", "build_prompt"]


class SupportResponse(BaseModel):
    verdict: str = Field(description="One of the four allowed verdict strings.")
    evidence_span_ids: list[str] = Field(
        max_length=3,
        description=(
            "At most three ids from the supplied abstract span list, verbatim. May be empty."
        ),
    )
    reason: str = Field(
        max_length=400,
        description="A brief interpretation shown to the researcher. Never treated as evidence.",
    )


SYSTEM = (
    "You judge whether an abstract supports a specific claim. You may answer only "
    "with one of: SUPPORTED, PARTIALLY_SUPPORTED, CONTRADICTED, "
    "UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE. Cite evidence only by the span ids you "
    "are given. Never quote or invent text. If the abstract cannot establish the "
    "claim either way, answer UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE."
)


def build_prompt(claim_text: str, spans: list[tuple[str, str]]) -> Prompt:
    listing = "\n".join(f"{span_id}: {text}" for span_id, text in spans)
    user = (
        f"{delimit('Claim from the manuscript', claim_text)}\n\n"
        f"{delimit('Cited work abstract, split into spans', listing)}\n\n"
        "Judge whether the abstract supports the claim, and list at most three span "
        "ids that justify your verdict."
    )
    return Prompt(name="support", version=PROMPT_VERSION, system=SYSTEM, user=user)
