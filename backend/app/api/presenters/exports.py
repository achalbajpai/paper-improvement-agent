from __future__ import annotations

from app.api.presenters.shared import _error_code
from app.api.schemas import ArtifactOut, ExportPreflight, ExportRunOut, PreflightItem, WarningOut
from app.db.models import ExportRun
from app.domain.lifecycle import CitationStyle, RunStatus
from app.services.exporter.preflight import Preflight as PreflightResult


def preflight(checks: PreflightResult) -> ExportPreflight:
    return ExportPreflight(
        revision_id=checks.revision_id,
        citation_style=checks.citation_style,
        can_export=checks.can_export,
        blockers=[
            PreflightItem(
                code=blocker.code,
                message=blocker.message,
                subject_ids=list(blocker.subject_ids),
            )
            for blocker in checks.blockers
        ],
        warnings=[
            WarningOut(
                id=warning.id,
                code=warning.code,
                message=warning.message,
                subject_ids=list(warning.subject_ids),
            )
            for warning in checks.warnings
        ],
    )


def export_run(run: ExportRun) -> ExportRunOut:
    return ExportRunOut(
        id=run.id,
        paper_id=run.paper_id,
        revision_id=run.revision_id,
        citation_style=CitationStyle(run.citation_style),
        status=RunStatus(run.status),
        failure_code=_error_code(run.failure_code),
        failure_detail=run.failure_detail,
        artifacts=[
            ArtifactOut(
                name=str(item.get("name", "")),
                media_type=str(item.get("media_type", "")),
                size_bytes=int(item.get("size_bytes", 0)),
                href=str(item.get("href", "")),
            )
            for item in run.artifacts
        ],
    )
