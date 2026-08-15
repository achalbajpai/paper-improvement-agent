"""HTTP translation for export preflight, export runs, and artifact downloads."""

from __future__ import annotations

from fastapi import APIRouter, Response
from fastapi.responses import FileResponse

from app.api.deps import IdempotencyKeyDep, SessionDep
from app.api.operations import complete_operation, release_operation
from app.api.presenters import export_run
from app.api.presenters import preflight as present_preflight
from app.api.schemas import CreateExportRequest, ExportPreflight, ExportRunOut
from app.db import repositories
from app.services.exporter import service

router = APIRouter(tags=["exports"])


@router.get("/papers/{paper_id}/export/preflight", response_model=ExportPreflight)
def export_preflight(session: SessionDep, paper_id: str) -> ExportPreflight:
    return present_preflight(service.preflight(session, paper_id))


@router.post("/papers/{paper_id}/exports", response_model=ExportRunOut)
def create_export(
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    paper_id: str,
    body: CreateExportRequest,
) -> ExportRunOut:
    outcome = repositories.begin_operation(
        session,
        scope_type="paper",
        scope_id=paper_id,
        operation_type="EXPORT_PAPER",
        idempotency_key=idempotency_key,
        request_body={
            "paper_id": paper_id,
            "acknowledged_warning_ids": sorted(body.acknowledged_warning_ids),
        },
    )
    if isinstance(outcome, repositories.ReplayedResponse):
        response.status_code = outcome.status_code
        return ExportRunOut.model_validate(outcome.body)

    operation_id = outcome.id
    session.commit()
    try:
        run = service.run_export(
            session, paper_id, acknowledged_warning_ids=body.acknowledged_warning_ids
        )
    except Exception:
        release_operation(session, operation_id)
        raise

    presented = export_run(run)
    complete_operation(session, operation_id, presented)
    return presented


@router.get("/exports/{run_id}", response_model=ExportRunOut)
def get_export(session: SessionDep, run_id: str) -> ExportRunOut:
    return export_run(repositories.get_export_run(session, run_id))


@router.get("/exports/{run_id}/artifacts/{name}")
def download_artifact(session: SessionDep, run_id: str, name: str) -> FileResponse:

    path = service.artifact_path(session, run_id, name)
    return FileResponse(path, filename=name)
