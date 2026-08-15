"""Both acceptance cycles, on a real paper, start to finish.

Upload, real GROBID, style selection, a shortening edit, a citation-adding edit,
and an export that produces a PDF -- through the same service functions the HTTP
handlers call, with no step simulated away.

What is real here and what is not is a deliberate line:

* **Real:** GROBID parsing the actual PDF, the postvalidator, the segmenter, the
  whole editing and verification path, Postgres with its constraints, Pandoc,
  citeproc, and xelatex. If any of those breaks, this test fails.
* **Recorded:** provider HTTP, patched at ``_send`` so that retry handling,
  429 semantics, decoding, and the error taxonomy all still execute against real
  response objects. Only the socket is replaced.
* **Scripted:** the LLM, returning fixtures validated against the production
  schemas. A model cannot be deterministic, and a test that depends on one tells
  you about today's sampling rather than about your code.

The point is reproducibility, not liveness. Evidence that the real providers and
a real model work lives in ``make live-smoke``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.orm import Session

from app.db import repositories
from app.domain.document import Document
from app.domain.lifecycle import CitationStyle, PaperStatus, RunStatus
from app.domain.proposal import ProposalState
from app.providers.llm import Prompt
from app.services.editor import proposals
from app.services.editor.acceptance import accept_proposal
from app.services.exporter import preflight, renderer
from app.services.exporter import service as export_service
from app.services.parser import pipeline
from app.settings import get_settings
from tests.support.llm import ScriptedLLM
from tests.test_export import pandoc_only

CORPUS_PDF = Path(__file__).resolve().parents[2] / "evals" / "papers" / "C_numeric_dense.pdf"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "providers"

pytestmark = [
    pytest.mark.usefixtures("database"),
    pytest.mark.slow,
    pytest.mark.skipif(
        not CORPUS_PDF.exists(),
        reason="the acceptance corpus is excluded from this checkout",
    ),
]


@pytest.fixture
def recorded_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Serve captured provider bodies, replacing only the socket.

    Patched at ``_send`` rather than at the client methods, so the bounded retry,
    the 429 path, JSON decoding, and every typed provider error above it still
    run exactly as they do in production. Swapping the client out instead would
    make this an assertion that our fakes agree with each other.
    """
    openalex = json.loads((FIXTURES / "openalex_search.json").read_text())
    semantic_scholar = json.loads((FIXTURES / "s2_search.json").read_text())

    def send(method: str, url: str, **kwargs: Any) -> httpx.Response:
        is_semantic_scholar = "semanticscholar" in url
        body = semantic_scholar if is_semantic_scholar else openalex
        status = 429 if is_semantic_scholar and "code" in body and "message" in body else 200
        return httpx.Response(
            status,
            json=body,
            request=httpx.Request(method, url),
            headers={"content-type": "application/json"},
        )

    monkeypatch.setattr("app.providers.http._send", send)


def _delimited(user_prompt: str) -> str:
    start = user_prompt.index("<<<BEGIN_UNTRUSTED_CONTENT>>>") + len(
        "<<<BEGIN_UNTRUSTED_CONTENT>>>"
    )
    return user_prompt[start : user_prompt.index("<<<END_UNTRUSTED_CONTENT>>>")].strip()


def _listed_ids(user_prompt: str) -> list[str]:
    """The identifiers the server offered, in the order it offered them.

    A scripted model must choose from what it was given, exactly as a real one
    must: anything else would pass a grounding check the production path applies.
    """
    found: list[str] = []
    for line in user_prompt.splitlines():
        head, separator, rest = line.partition(": ")
        if separator and head and " " not in head and rest.strip():
            found.append(head)
    return found


def shortening_model(section_id: str) -> ScriptedLLM:
    """Routes to SHORTEN_SECTION and shortens by dropping trailing sentences.

    Extractive by construction: it only ever returns a prefix of the sentences it
    was given, so no protected token can be damaged and nothing can be invented.
    That is what makes the outcome deterministic without weakening any check.
    """

    def rewrite(prompt: Prompt) -> dict[str, Any]:
        sentences = _delimited(prompt.user).split(". ")
        keep = sentences[: max(1, len(sentences) * 2 // 3)]
        text = ". ".join(keep)
        return {
            "revised_text": text if text.endswith(".") else f"{text}.",
            "claimed_actions": ["removed trailing sentences"],
        }

    return ScriptedLLM(
        {
            "intent": {
                "intent": "SHORTEN_SECTION",
                "section_id": section_id,
                "target_reduction_ratio": 0.2,
                "confidence": "HIGH",
                "reason": "The command names a section and a reduction.",
            },
            "rewrite": rewrite,
            "novelty": {"has_unsupported_novelty": False, "findings": []},
            "attachment": {"verdict": "SUPPORTED", "reason": "The sentence is unchanged."},
        }
    )


def citing_model(section_id: str) -> ScriptedLLM:
    """Routes to ADD_SUPPORTING_CITATIONS and attaches the first offered work."""

    def claims(prompt: Prompt) -> dict[str, Any]:
        return {
            "claims": [
                {
                    "sentence_id": sentence_id,
                    "is_citable_claim": True,
                    "topic": "deep residual learning for image recognition",
                }
                for sentence_id in _listed_ids(prompt.user)[:1]
            ]
        }

    def rerank(prompt: Prompt) -> dict[str, Any]:
        return {
            "ranked": [
                {
                    "candidate_id": candidate_id,
                    "rank": index + 1,
                    "rationale": "This work addresses the method the claim relies on.",
                }
                for index, candidate_id in enumerate(_listed_ids(prompt.user)[:3])
            ]
        }

    def selection(prompt: Prompt) -> dict[str, Any]:
        ids = _listed_ids(prompt.user)
        sentences = [item for item in ids if item.startswith("p_")]
        candidates = [item for item in ids if item.startswith("cand_")]
        if not sentences or not candidates:
            return {"selections": []}
        return {
            "selections": [
                {
                    "target_sentence_id": sentences[0],
                    "candidate_id": candidates[0],
                    "rationale": "This work introduces the method the sentence describes.",
                }
            ]
        }

    def support(prompt: Prompt) -> dict[str, Any]:
        spans = [item for item in _listed_ids(prompt.user) if item.startswith("span_")]
        return {
            "verdict": "SUPPORTED",
            "evidence_span_ids": spans[:1],
            "reason": "The abstract states the result the sentence relies on.",
        }

    return ScriptedLLM(
        {
            "intent": {
                "intent": "ADD_SUPPORTING_CITATIONS",
                "section_id": section_id,
                "confidence": "HIGH",
                "reason": "The command asks for citations in a named section.",
            },
            "claims": claims,
            "rerank": rerank,
            "selection": selection,
            "support": support,
            "attachment": {"verdict": "SUPPORTED", "reason": "The sentence is unchanged."},
        }
    )


@pandoc_only
def test_two_acceptance_cycles_end_in_an_exported_pdf(
    db: Session, recorded_providers: None
) -> None:
    """Upload to PDF, with both intents accepted along the way."""
    paper = pipeline.create_paper(
        db, filename="C_numeric_dense.pdf", content=CORPUS_PDF.read_bytes()
    )
    db.commit()

    pipeline.parse_paper(db, paper.id, operation_id=repositories.new_id("op"))
    assert paper.active_parse_op_id is None
    db.refresh(paper)
    assert paper.status == PaperStatus.PARSED.value

    revision = repositories.get_current_revision(db, paper)
    document = Document.model_validate(revision.document)
    assert document.sections and document.references
    assert revision.revision_number == 1
    storage = get_settings().papers_dir / paper.storage_id
    source_asset_paths = [
        storage / block.source_asset for block in document.blocks.values() if block.source_asset
    ]
    assert source_asset_paths
    assert all(path.is_file() for path in source_asset_paths)

    assert paper.detected_citation_style == CitationStyle.IEEE.value
    assert paper.citation_style is None
    blocked = export_service.preflight(db, paper.id)
    assert not blocked.can_export
    assert preflight.BLOCK_NO_STYLE_SELECTED in {item.code for item in blocked.blockers}

    paper.citation_style = CitationStyle.IEEE.value
    db.commit()

    section = _editable_section(document)

    first = proposals.create_proposal(
        db,
        paper.id,
        "Shorten that section by about 20%",
        operation_id=repositories.new_id("op"),
        llm=shortening_model(section),
    )
    assert first.state == ProposalState.AWAITING_DECISION.value, first.failure_code
    assert first.candidate is not None

    words_before = document.word_count()
    accepted = accept_proposal(
        db, first.id, acknowledged_warning_ids=list(first.required_warning_ids)
    )
    assert accepted.revision_number == 2

    db.refresh(paper)
    shortened = Document.model_validate(repositories.get_current_revision(db, paper).document)
    assert shortened.word_count() < words_before

    assert len(shortened.references) == len(document.references)

    second = proposals.create_proposal(
        db,
        paper.id,
        "Add supporting citations to that section",
        operation_id=repositories.new_id("op"),
        llm=citing_model(_editable_section(shortened)),
    )
    assert second.state == ProposalState.AWAITING_DECISION.value, second.failure_code

    accept_proposal(db, second.id, acknowledged_warning_ids=list(second.required_warning_ids))
    db.refresh(paper)
    final = Document.model_validate(repositories.get_current_revision(db, paper).document)

    assert repositories.get_current_revision(db, paper).revision_number == 3
    assert len(final.citations) > len(shortened.citations)
    assert len(final.references) > len(shortened.references)

    assert final.word_count() == shortened.word_count()

    checks = export_service.preflight(db, paper.id)
    assert checks.can_export, [blocker.code for blocker in checks.blockers]

    run = export_service.run_export(
        db, paper.id, acknowledged_warning_ids=list(checks.required_warning_ids)
    )
    assert run.status == RunStatus.COMPLETED.value
    assert {artifact["name"] for artifact in run.artifacts} == set(renderer.ARTIFACT_NAMES)

    pdf = export_service.artifact_path(db, run.id, "revised.pdf")
    assert pdf.read_bytes()[:4] == b"%PDF"
    assert pdf.stat().st_size > 10_000


def _editable_section(document: Document) -> str:
    """The first section with enough prose to be worth shortening."""
    for section in document.sections:
        words = sum(len(paragraph.text.split()) for paragraph in section.paragraphs)
        if words > 200:
            return section.id
    raise AssertionError("this corpus paper has no section long enough to edit")
