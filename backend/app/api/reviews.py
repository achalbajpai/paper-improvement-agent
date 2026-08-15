"""HTTP translation for review runs."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Response
from sqlalchemy import select

from app.api.deps import IdempotencyKeyDep, SessionDep
from app.api.operations import complete_operation, release_operation
from app.api.presenters import finding, review_run
from app.api.schemas import FindingOut, HandleFindingRequest, ReviewRunOut
from app.db import repositories
from app.db.models import DocumentRevision, ReviewFindingRow, SourceRecord
from app.domain.document import Document
from app.domain.errors import NotFoundError
from app.services.review import service

router = APIRouter(tags=["reviews"])


@router.post("/papers/{paper_id}/reviews", response_model=ReviewRunOut)
def create_review(
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    paper_id: str,
) -> ReviewRunOut:
    outcome = repositories.begin_operation(
        session,
        scope_type="paper",
        scope_id=paper_id,
        operation_type="REVIEW_PAPER",
        idempotency_key=idempotency_key,
        request_body={"paper_id": paper_id},
    )
    if isinstance(outcome, repositories.ReplayedResponse):
        response.status_code = outcome.status_code
        return ReviewRunOut.model_validate(outcome.body)

    operation_id = outcome.id
    session.commit()
    try:
        run = service.start_review(session, paper_id, operation_id=operation_id)
    except Exception:
        release_operation(session, operation_id)
        raise

    body = review_run(session, run)
    complete_operation(session, operation_id, body)
    return body


@router.get("/papers/{paper_id}/reviews", response_model=list[ReviewRunOut])
def list_reviews(session: SessionDep, paper_id: str) -> list[ReviewRunOut]:
    repositories.get_paper(session, paper_id)
    return [review_run(session, run) for run in repositories.list_review_runs(session, paper_id)]


@router.get("/reviews/{run_id}", response_model=ReviewRunOut)
def get_review(session: SessionDep, run_id: str) -> ReviewRunOut:
    return review_run(session, repositories.get_review_run(session, run_id))


@router.patch("/findings/{finding_id}", response_model=FindingOut)
def handle_finding(session: SessionDep, finding_id: str, body: HandleFindingRequest) -> FindingOut:
    """Mark one finding as dealt with, or put it back.

    The finding stays in its run and in the run's counts.
    """
    row = session.get(ReviewFindingRow, finding_id)
    if row is None:
        raise NotFoundError("No such finding.", finding_id=finding_id)

    row.handled_at = datetime.now(UTC) if body.handled else None
    session.commit()

    run = repositories.get_review_run(session, row.run_id)
    revision = session.get(DocumentRevision, run.revision_id)
    document = Document.model_validate(revision.document) if revision else None
    sources = {
        record.id: record
        for record in session.execute(
            select(SourceRecord).where(SourceRecord.paper_id == row.paper_id)
        )
        .scalars()
        .all()
    }
    return finding(row, document, sources)
