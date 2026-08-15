"""The review operation, fenced.

Shaped like the parse: a short transaction to establish what is being reviewed,
the slow work with no transaction open, then a short transaction to write. A
review takes minutes of provider and model calls, and holding a row lock across
that would pin a connection for the whole run.

A failed review still writes a run row. "The review errored" and "the review
found nothing" are different states, and a run that vanished on failure leaves
the researcher unable to tell them apart. The handler catches every exception
rather than only the typed ones: a failure this system did not anticipate is
exactly the one whose run row would otherwise be missing.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import ReviewRun
from app.db.session import session_scope
from app.domain.document import Document
from app.domain.errors import InvalidStateTransitionError, as_app_error
from app.domain.lifecycle import PaperStatus
from app.observability import get_logger
from app.providers.llm import StructuredLLM, build_llm
from app.providers.session import ProviderSession
from app.services.deadline import Deadline
from app.services.review.runner import ReviewRunner, claim_run, persist, record_failure
from app.settings import get_settings

logger = get_logger(__name__)


@dataclass(frozen=True)
class _Claim:
    """What a claimed run needs to execute, detached from any session."""

    run_id: str
    revision_id: str
    document: Document


def _claim(session: Session, paper_id: str) -> _Claim:
    paper = repositories.get_paper(session, paper_id)
    if paper.status != PaperStatus.PARSED.value:
        raise InvalidStateTransitionError(
            "This paper must be parsed before it can be reviewed.",
            paper_id=paper_id,
            status=paper.status,
        )
    revision = repositories.get_current_revision(session, paper)
    return _Claim(
        run_id=claim_run(session, paper_id=paper_id, revision_id=revision.id).id,
        revision_id=revision.id,
        document=Document.model_validate(revision.document),
    )


def start_review(
    session: Session,
    paper_id: str,
    *,
    operation_id: str,
    llm: StructuredLLM | None = None,
) -> ReviewRun:
    """Claim a run, return it PENDING, and continue the work off the request.

    A review is minutes of model and provider calls, longer than a request should
    be held open. The run row is committed first, so status is pollable at once.

    A daemon thread rather than a queue: one API process, and an interrupted run is
    already reconciled to a terminal state at startup.
    """
    claim = _claim(session, paper_id)
    session.commit()
    run = repositories.get_review_run(session, claim.run_id)

    thread = threading.Thread(
        target=_execute_detached,
        args=(paper_id, claim, operation_id, llm),
        name=f"review-{claim.run_id}",
        daemon=True,
    )
    thread.start()
    return run


def _execute_detached(
    paper_id: str,
    claim: _Claim,
    operation_id: str,
    llm: StructuredLLM | None,
) -> None:
    """Run the slow half on its own session, swallowing what the caller cannot see."""
    try:
        with session_scope() as session:
            _execute(session, paper_id, claim, operation_id=operation_id, llm=llm)
    except Exception:
        logger.warning("review.worker_failed", extra={"paper_id": paper_id})


def run_review(
    session: Session,
    paper_id: str,
    *,
    operation_id: str,
    llm: StructuredLLM | None = None,
) -> ReviewRun:
    """Review the current revision synchronously. Used by the live smoke."""
    claim = _claim(session, paper_id)
    session.commit()
    return _execute(session, paper_id, claim, operation_id=operation_id, llm=llm)


def _execute(
    session: Session,
    paper_id: str,
    claim: _Claim,
    *,
    operation_id: str,
    llm: StructuredLLM | None = None,
) -> ReviewRun:
    settings = get_settings()
    deadline = Deadline.after("review", settings.review_deadline_seconds)
    run_id, revision_id, document = claim.run_id, claim.revision_id, claim.document

    provider_session = ProviderSession(operation_id=operation_id)
    runner = ReviewRunner(
        session, paper_id, llm=llm or build_llm(), provider_session=provider_session
    )

    try:
        outcome = runner.run(document, deadline)
        run = persist(
            session,
            run_id=run_id,
            paper_id=paper_id,
            outcome=outcome,
            provider_session=provider_session,
        )
        session.commit()
    except Exception as error:
        code = as_app_error(error).code
        session.rollback()
        failed = record_failure(
            session,
            run_id=run_id,
            paper_id=paper_id,
            revision_id=revision_id,
            failure_code=code.value,
            provider_session=provider_session,
        )
        session.commit()
        logger.warning(
            "review.failed",
            extra={
                "paper_id": paper_id,
                "run_id": failed.id,
                "code": code.value,
                **as_app_error(error).details,
            },
        )
        raise
    logger.info(
        "review.completed",
        extra={
            "paper_id": paper_id,
            "run_id": run.id,
            "findings": len(outcome.findings),
            "degraded": [item.provider for item in outcome.degradations],
        },
    )
    return run
