"""The whole product, against everything real, narrated.

``test_e2e_smoke`` proves the same path is *correct*; it scripts the model and
replays recorded provider bodies, because a test that depends on today's
sampling tells you about the sampler rather than about your code. This script
proves the path is *live*: real GROBID, real OpenAlex and Semantic Scholar over
the network, a real language model choosing what to shorten and what to cite,
real Pandoc and xelatex producing a PDF at the end.

So it is never part of ``make test``. It costs money, it depends on three third
parties being up, and it can legitimately fail for reasons that are not defects
-- Semantic Scholar rate-limits unauthenticated callers as a matter of course.
Those outcomes are reported as what they are rather than crashing the run,
because "the provider was rate-limited" is a thing this system is supposed to be
able to say out loud.

Two acceptance cycles, both intents, ending in a PDF on disk::

    make live-smoke
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import EditProposal, Paper
from app.db.session import init_db, session_scope
from app.domain.document import Document, Section
from app.domain.errors import AppError
from app.domain.lifecycle import CitationStyle, PaperStatus, RunStatus
from app.domain.proposal import CandidateRevisionSnapshot, ProposalState
from app.domain.verification import CheckStatus
from app.services.editor import proposals
from app.services.editor.acceptance import accept_proposal
from app.services.exporter import service as export_service
from app.services.parser import pipeline
from app.services.review import service as review_service
from app.settings import get_settings

CORPUS = Path("/evals/papers/C_numeric_dense.pdf")


def main() -> int:
    settings = get_settings()
    if not settings.llm_configured:
        print("No LLM API key is configured. Review and editing cannot run.")
        print("Add one to .env and `docker compose up -d api` to run this walkthrough.")
        return 2
    if not CORPUS.exists():
        print(f"Missing {CORPUS}. Run `make corpus` first.")
        return 2

    init_db()
    with session_scope() as session:
        return walkthrough(session)


def walkthrough(session: Session) -> int:
    step("Uploading the paper")
    paper = pipeline.create_paper(session, filename=CORPUS.name, content=CORPUS.read_bytes())
    session.commit()
    print(f"  {paper.id}")

    step("Parsing it with GROBID")
    started = time.monotonic()
    pipeline.parse_paper(session, paper.id, operation_id=repositories.new_id("op"))
    session.refresh(paper)
    if paper.status != PaperStatus.PARSED.value:
        return fail(f"parse left the paper {paper.status}")
    document = current(session, paper)
    print(
        f"  {len(document.sections)} sections, {len(document.citations)} citation markers, "
        f"{len(document.references)} references, in {time.monotonic() - started:.0f}s"
    )
    print(
        f"  detected style: {paper.detected_citation_style} "
        f"({paper.detected_style_confidence} confidence), not yet chosen"
    )

    step("Choosing a citation style, which detection only suggested")
    paper.citation_style = CitationStyle.IEEE.value
    session.commit()
    print(f"  {paper.citation_style}")

    review(session, paper)

    section = editable_section(document)

    step("Cycle one: shortening a section")
    words_before = document.word_count()
    if cycle(session, paper, f"Shorten the '{section.title}' section by about 20%") != 0:
        return 1
    shortened = current(session, paper)
    print(
        f"  {words_before} words -> {shortened.word_count()}, "
        f"{len(shortened.references)} references still in the bibliography"
    )

    step("Cycle two: adding supporting citations")
    if cycle(session, paper, f"Add supporting citations to the '{section.title}' section") != 0:
        return 1
    final = current(session, paper)
    print(
        f"  {len(final.citations) - len(shortened.citations)} markers added, "
        f"{len(final.references) - len(shortened.references)} new references, prose unchanged "
        f"({final.word_count()} words, was {shortened.word_count()})"
    )

    return export(session, paper)


def review(session: Session, paper: Paper) -> None:
    """Never fatal: a review that could not run does not invalidate the rest."""
    step("Reviewing it against real academic search")
    try:
        run = review_service.run_review(session, paper.id, operation_id=repositories.new_id("op"))
    except AppError as error:
        print(f"  review failed: {error.code} -- {error}")
        return
    print(f"  {run.status}, {len(run.findings)} findings")
    for finding in run.findings[:5]:
        print(f"    {finding.kind:24} {finding.verdict or '-':38} {finding.reference_id or ''}")
    for degradation in run.degradations:
        print(f"  degraded: {degradation}")


def cycle(session: Session, paper: Paper, command: str) -> int:
    """Propose, show what the researcher would see, accept."""
    proposal = proposals.create_proposal(
        session, paper.id, command, operation_id=repositories.new_id("op")
    )
    if proposal.state == ProposalState.FAILED.value:
        print(f"  refused: {proposal.failure_code} -- {proposal.failure_detail or ''}")
        return 1
    if proposal.state != ProposalState.AWAITING_DECISION.value:
        return fail(f"proposal came back {proposal.state}: {proposal.failure_code}")

    report(proposal)
    accept_proposal(
        session, proposal.id, acknowledged_warning_ids=list(proposal.required_warning_ids)
    )
    session.refresh(paper)
    return 0


def export(session: Session, paper: Paper) -> int:
    step("Exporting")
    checks = export_service.preflight(session, paper.id)
    for blocker in checks.blockers:
        print(f"  blocked: {blocker.code} -- {blocker.message}")
    for warning in checks.warnings:
        print(f"  warning: {warning.message}")
    if not checks.can_export:
        return fail("export is blocked")

    run = export_service.run_export(
        session, paper.id, acknowledged_warning_ids=list(checks.required_warning_ids)
    )
    if run.status != RunStatus.COMPLETED.value:
        return fail(f"export finished {run.status}: {run.failure_code}")
    for artifact in run.artifacts:
        print(f"  {artifact['name']:16} {artifact['size_bytes']:>9,} bytes")

    pdf = export_service.artifact_path(session, run.id, "revised.pdf")
    if pdf.read_bytes()[:4] != b"%PDF":
        return fail(f"{pdf} is not a PDF")

    revision = repositories.get_current_revision(session, paper)
    print(f"\nDone. Revision {revision.revision_number} exported to {pdf}")
    return 0


def step(title: str) -> None:
    print(f"\n== {title}")


def fail(message: str) -> int:
    print(f"\nFAILED: {message}")
    return 1


def report(proposal: EditProposal) -> None:
    """What the researcher would see before deciding."""
    if proposal.candidate is None:
        return
    snapshot = CandidateRevisionSnapshot.model_validate(proposal.candidate)
    print(f"  {len(snapshot.delta.changes)} changes, computed by comparing the two documents")
    for check in snapshot.verification.checks:
        if check.status != CheckStatus.PASSED:
            print(f"    {check.status:8} {check.name}: {check.detail}")
    for warning in snapshot.verification.warnings:
        print(f"    must acknowledge: {warning.message}")


def current(session: Session, paper: Paper) -> Document:
    return Document.model_validate(repositories.get_current_revision(session, paper).document)


def editable_section(document: Document) -> Section:
    for section in document.sections:
        words = sum(len(paragraph.text.split()) for paragraph in section.paragraphs)
        if words > 200:
            return section
    raise SystemExit("this paper has no section long enough to be worth shortening")


if __name__ == "__main__":
    sys.exit(main())
