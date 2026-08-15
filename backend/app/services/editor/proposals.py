"""Turning one command into one proposal row.

The row is written **before** the work starts. A command that finds nothing, or
errors, or is refused, still leaves a record saying which command it was and what
happened to it -- because the alternative is a researcher who typed something,
waited, and got a page with nothing new on it and no way to tell whether the
system had declined, failed, or found nothing.

That is why ``NO_RESULTS`` lands here as ``FAILED`` with a code and no candidate
snapshot. The absent candidate is the signal that no edit exists to review; the
code is the signal that the search ran and came back empty, as distinct from a
provider that could not be reached.

Shaped like the review: a short transaction to establish what is being edited,
the slow model and provider work with no transaction open, then a short
transaction to write the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import EditProposal, VerificationCheckRow
from app.domain.delta import ComputedEditDelta
from app.domain.document import Document
from app.domain.errors import (
    AppError,
    ErrorCode,
    InvalidStateTransitionError,
    OperationInProgressError,
    as_app_error,
)
from app.domain.lifecycle import PaperStatus
from app.domain.proposal import (
    CandidateRevisionSnapshot,
    EditScope,
    ProposalState,
    SkippedParagraph,
)
from app.domain.verification import VerificationCheck
from app.observability import get_logger
from app.providers.llm import StructuredLLM, build_llm
from app.providers.session import ProviderSession
from app.services.citations.sources import SourceStore, record_attempts
from app.services.deadline import Deadline
from app.services.editor.addcite import AddCitationsResult, CitationAdder
from app.services.editor.intent import EditIntent
from app.services.editor.router import RoutedCommand, route
from app.services.editor.shorten import ShortenResult, shorten_section, target_shortfall
from app.services.editor.verification import VerificationInputs, verify
from app.services.research.retrieval import AcademicRetrievalService
from app.settings import get_settings

logger = get_logger(__name__)


DECIDED_OUTCOMES = frozenset(
    {
        ErrorCode.NO_RESULTS,
        ErrorCode.UNSUPPORTED_INTENT,
        ErrorCode.AMBIGUOUS_INTENT,
        ErrorCode.UNSUPPORTED_NOVELTY,
        ErrorCode.PROTECTED_TOKEN_VIOLATION,
        ErrorCode.PROSE_MUTATION_FORBIDDEN,
    }
)


@dataclass
class EditOutcome:
    """What an intent produced, before anything decides whether to offer it."""

    intent: EditIntent
    document: Document
    delta: ComputedEditDelta
    checks: list[VerificationCheck]
    section_id: str | None = None
    paragraph_id: str | None = None
    shorten: ShortenResult | None = None
    addcite: AddCitationsResult | None = None


def create_proposal(
    session: Session,
    paper_id: str,
    command: str,
    *,
    operation_id: str,
    llm: StructuredLLM | None = None,
    target_section_id: str | None = None,
    target_paragraph_id: str | None = None,
) -> EditProposal:
    """Run one command against the paper's current revision.

    A target is present when the researcher chose one -- usually by answering the
    question a previous ambiguous command raised. It binds where the command
    acts; the command itself still says what to do.
    """
    settings = get_settings()
    deadline = Deadline.after("edit", settings.proposal_deadline_seconds)
    model: StructuredLLM = llm or build_llm()

    repositories.lock_paper(session, paper_id)
    paper = repositories.get_paper(session, paper_id)
    if paper.status != PaperStatus.PARSED.value:
        raise InvalidStateTransitionError(
            "This paper must be parsed before it can be edited.",
            paper_id=paper_id,
            status=paper.status,
        )
    _refuse_overlap(session, paper_id)

    revision = repositories.get_current_revision(session, paper)
    revision_id = revision.id
    document = Document.model_validate(revision.document)

    proposal = EditProposal(
        id=repositories.new_id("prop"),
        paper_id=paper_id,
        base_revision_id=revision_id,
        command=command,
        state=ProposalState.PENDING.value,
    )
    session.add(proposal)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise OperationInProgressError(
            "This paper already has an edit awaiting a decision.", paper_id=paper_id
        ) from error
    proposal_id = proposal.id

    provider_session = ProviderSession(operation_id=operation_id)
    routed: RoutedCommand | None = None
    try:
        routed = route(
            command,
            document,
            model,
            deadline,
            target_section_id=target_section_id,
            target_paragraph_id=target_paragraph_id,
        )
        outcome = _run(
            session,
            paper_id,
            document,
            routed,
            llm=model,
            provider_session=provider_session,
            deadline=deadline,
        )
        verification = verify(
            VerificationInputs(
                paper_id=paper_id,
                base=document,
                candidate=outcome.document,
                delta=outcome.delta,
                added=tuple(outcome.addcite.added) if outcome.addcite else (),
                degradations=provider_session.degradations,
                achieved_fraction=(
                    target_shortfall(outcome.shorten.plan, outcome.delta)
                    if outcome.shorten
                    else None
                ),
                prior_checks=tuple(outcome.checks),
            ),
            session=session,
            llm=model,
            deadline=deadline,
        )
        snapshot = CandidateRevisionSnapshot(
            base_revision_id=revision_id,
            document=outcome.document,
            delta=outcome.delta,
            verification=verification,
            explanation=_explain(outcome, document),
            scope=_scope(outcome, document),
        )

        proposal = repositories.get_proposal(session, proposal_id)
        proposal.intent = outcome.intent.value
        proposal.candidate = snapshot.model_dump(mode="json")
        proposal.candidate_sha256 = snapshot.snapshot_sha256

        proposal.required_warning_ids = list(verification.required_warning_ids)

        proposal.state = (
            ProposalState.BLOCKED.value
            if verification.is_blocked
            else ProposalState.AWAITING_DECISION.value
        )
        proposal.failure_code = None
        proposal.failure_detail = None
        _record_checks(session, proposal_id, verification.checks)
        record_attempts(session, paper_id, provider_session)
        session.commit()
    except Exception as error:
        typed = as_app_error(error)
        session.rollback()
        failed = _fail(
            session,
            proposal_id,
            typed,
            intent=routed.intent if routed else None,
            provider_session=provider_session,
        )
        if typed.code in DECIDED_OUTCOMES:
            return failed
        raise

    logger.info(
        "proposal.candidate_built",
        extra={
            "paper_id": paper_id,
            "proposal_id": proposal.id,
            "intent": outcome.intent.value,
            "changes": len(outcome.delta.changes),
            "state": proposal.state,
            "warnings": len(verification.warnings),
            "blockers": [blocker.code.value for blocker in verification.blockers],
        },
    )
    return proposal


def _record_checks(
    session: Session, proposal_id: str, checks: tuple[VerificationCheck, ...]
) -> None:
    """Store each check individually, so a stored verdict stays traceable."""
    for check in checks:
        session.add(
            VerificationCheckRow(
                id=repositories.new_id("chk"),
                proposal_id=proposal_id,
                name=check.name.value,
                status=check.status.value,
                detail=check.detail,
                prompt_version=check.prompt_version,
                model=check.model,
                provider=check.provider,
            )
        )


def _explain(outcome: EditOutcome, document: Document) -> str:
    """A sentence for the researcher, shown beside the computed delta.

    Never instead of it. The delta is what happened; this is only a readable
    summary of the intent that produced it.

    It names what was edited, because "shortened by 126 words" is the same
    sentence whether one paragraph changed or the whole paper did.
    """
    if outcome.shorten is not None:
        removed = outcome.delta.scope_words_before - outcome.delta.scope_words_after
        mode = " by removing whole sentences" if outcome.shorten.extractive_only else ""
        return f"Shortened {_where(outcome, document)} by {removed} words{mode}."
    if outcome.addcite is not None:
        count = len(outcome.addcite.added)
        plural = "" if count == 1 else "s"
        return (
            f"Added {count} supporting citation{plural} to {_where(outcome, document)} "
            "without changing any prose."
        )
    return ""


def _where(outcome: EditOutcome, document: Document) -> str:
    scope = _scope(outcome, document)
    if scope is None:
        return "the paper"
    named = scope.section_title or scope.section_id
    if scope.paragraph_id is not None:
        return f"one paragraph of {named}"
    return named


def _scope(outcome: EditOutcome, document: Document) -> EditScope | None:
    """The planner's decisions, in the shape the researcher reads them.

    Only shortening has a plan; adding citations selects sentences rather than
    allocating a reduction, so it reports the section it acted on and nothing
    about paragraphs it declined to touch.
    """
    section_id = (
        outcome.shorten.plan.section_id if outcome.shorten is not None else outcome.section_id
    )
    if section_id is None:
        return None

    section = document.section(section_id)
    common = {
        "section_id": section_id,
        "section_title": section.title if section else "",
        "section_paragraph_count": len(section.paragraphs) if section else 0,
        "paragraph_id": outcome.paragraph_id,
    }
    if outcome.shorten is None:
        return EditScope(**common)

    plan = outcome.shorten.plan
    return EditScope(
        **common,
        targeted_paragraph_ids=tuple(target.paragraph_id for target in plan.targets),
        skipped=tuple(
            SkippedParagraph(paragraph_id=paragraph_id, reason=reason)
            for paragraph_id, reason in plan.skipped
        ),
        words_before=plan.words_before,
        words_target=plan.words_target,
    )


def _run(
    session: Session,
    paper_id: str,
    document: Document,
    routed: RoutedCommand,
    *,
    llm: StructuredLLM,
    provider_session: ProviderSession,
    deadline: Deadline,
) -> EditOutcome:
    if routed.section_id is None:
        raise InvalidStateTransitionError("The command named no section to edit.")

    if routed.intent is EditIntent.SHORTEN_SECTION:
        result = shorten_section(
            document,
            routed.section_id,
            llm=llm,
            deadline=deadline,
            ratio=routed.target_reduction_ratio,
            paragraph_id=routed.paragraph_id,
        )
        return EditOutcome(
            intent=routed.intent,
            document=result.document,
            delta=result.delta,
            checks=result.checks,
            section_id=routed.section_id,
            paragraph_id=routed.paragraph_id,
            shorten=result,
        )

    adder = CitationAdder(
        llm=llm,
        retrieval=AcademicRetrievalService(provider_session),
        sources=SourceStore(session, paper_id),
    )
    added = adder.run(document, routed.section_id, deadline)
    return EditOutcome(
        intent=routed.intent,
        document=added.document,
        delta=added.delta,
        checks=added.checks,
        section_id=routed.section_id,
        addcite=added,
    )


def _fail(
    session: Session,
    proposal_id: str,
    error: AppError,
    *,
    intent: EditIntent | None,
    provider_session: ProviderSession,
) -> EditProposal:
    """Record why the command produced nothing, and leave no candidate.

    The two facts a researcher needs are both here: the code says what happened,
    and the null candidate says there is nothing to review. Neither is inferable
    from the other, so both are stored.

    The intent is written here rather than when routing returned it, because the
    rollback that precedes this call discards anything staged before the failure.
    """
    proposal = repositories.get_proposal(session, proposal_id)
    proposal.state = ProposalState.FAILED.value
    proposal.intent = intent.value if intent is not None else None
    proposal.failure_code = error.code.value
    proposal.failure_detail = _readable(error)
    proposal.candidate = None
    proposal.candidate_sha256 = None
    record_attempts(session, proposal.paper_id, provider_session)
    session.commit()
    logger.warning(
        "proposal.failed",
        extra={"proposal_id": proposal_id, "code": error.code.value},
    )
    return proposal


def _readable(error: AppError) -> str:
    """The most specific thing the failure knows about itself.

    An ambiguous command carries a clarification naming what the researcher must
    choose between, and that is the entire useful content of the failure. It used
    to be computed by the router, packed into ``details``, and then dropped here
    in favour of ``str(error)`` -- so the system worked out the question it needed
    to ask and then declined to ask it.
    """
    clarification = str(error.details.get("clarification") or "").strip()
    return (clarification or str(error))[:500]


def _refuse_overlap(session: Session, paper_id: str) -> None:
    """One edit at a time per paper.

    Two proposals built against the same revision would both be valid and only
    one could be accepted, so the second is refused now rather than at acceptance
    after the researcher has read it.

    Callers must hold the paper lock. This read alone cannot exclude a
    concurrent insert; the partial unique index on ``edit_proposals`` is what
    makes the invariant true, and this is what makes losing it rare and the
    error message good.
    """
    active = repositories.active_proposals(session, paper_id)
    if active:
        raise OperationInProgressError(
            "This paper already has an edit awaiting a decision.",
            paper_id=paper_id,
            proposal_id=active[0].id,
        )
