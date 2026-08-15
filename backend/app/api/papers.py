from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile, status

from app.api.deps import IdempotencyKeyDep, SessionDep
from app.api.operations import complete_operation, release_operation
from app.api.presenters import manuscript, paper_detail, paper_summary
from app.api.schemas import (
    ManuscriptOut,
    PaperDetail,
    PaperSummary,
    SetCitationStyleRequest,
)
from app.db import repositories
from app.db.models import ReferenceResolution
from app.domain.errors import MalformedRequestError
from app.services.parser import pipeline
from app.settings import get_settings

router = APIRouter(tags=["papers"])


@router.post("/papers", response_model=PaperSummary, status_code=status.HTTP_201_CREATED)
def upload_paper(
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    file: Annotated[UploadFile, File()],
    filename: Annotated[str | None, Form()] = None,
) -> PaperSummary:
    content = file.file.read(get_settings().max_upload_bytes + 1)
    if not content:
        raise MalformedRequestError("The uploaded file is empty.")
    pipeline.validate_upload(content)

    outcome = repositories.begin_operation(
        session,
        scope_type="global",
        scope_id=None,
        operation_type="UPLOAD_PAPER",
        idempotency_key=idempotency_key,
        request_body={"sha256": pipeline.content_digest(content)},
    )
    if isinstance(outcome, repositories.ReplayedResponse):
        response.status_code = outcome.status_code
        return PaperSummary.model_validate(outcome.body)

    paper = pipeline.create_paper(
        session, filename=filename or file.filename or "manuscript.pdf", content=content
    )
    body = paper_summary(paper)
    complete_operation(
        session,
        outcome.id,
        body,
        status_code=status.HTTP_201_CREATED,
    )
    return body


@router.get("/papers", response_model=list[PaperSummary])
def list_papers(session: SessionDep) -> list[PaperSummary]:
    return [paper_summary(paper) for paper in repositories.list_papers(session)]


@router.get("/papers/{paper_id}", response_model=PaperDetail)
def get_paper(session: SessionDep, paper_id: str) -> PaperDetail:
    return paper_detail(session, repositories.get_paper(session, paper_id))


@router.get("/papers/{paper_id}/manuscript", response_model=ManuscriptOut)
def get_manuscript(session: SessionDep, paper_id: str) -> ManuscriptOut:
    paper = repositories.get_paper(session, paper_id)
    revision = repositories.get_current_revision(session, paper)
    resolutions = {
        row.reference_id: row
        for row in session.query(ReferenceResolution).filter_by(paper_id=paper_id)
    }
    return manuscript(paper, revision, resolutions)


@router.post("/papers/{paper_id}/parse", response_model=PaperDetail)
def parse_paper(
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    paper_id: str,
) -> PaperDetail:
    outcome = repositories.begin_operation(
        session,
        scope_type="paper",
        scope_id=paper_id,
        operation_type="PARSE_PAPER",
        idempotency_key=idempotency_key,
        request_body={"paper_id": paper_id},
    )
    if isinstance(outcome, repositories.ReplayedResponse):
        response.status_code = outcome.status_code
        return PaperDetail.model_validate(outcome.body)

    operation_id = outcome.id
    session.commit()
    try:
        pipeline.parse_paper(session, paper_id, operation_id=operation_id)
    except Exception:
        release_operation(session, operation_id, rollback=True)
        raise

    body = paper_detail(session, repositories.get_paper(session, paper_id))
    complete_operation(session, operation_id, body)
    return body


@router.patch("/papers/{paper_id}/citation-style", response_model=PaperDetail)
def set_citation_style(
    session: SessionDep, paper_id: str, request: SetCitationStyleRequest
) -> PaperDetail:
    paper = repositories.get_paper(session, paper_id)
    paper.citation_style = request.citation_style.value
    session.commit()
    return paper_detail(session, paper)
