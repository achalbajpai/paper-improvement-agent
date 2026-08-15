"""The export operation, fenced.

An export run row is written before rendering starts and updated when it ends, so
a failed export is a run that says why rather than a download button that does
nothing. Rendering happens with no transaction open: Pandoc plus pdfTeX takes
seconds, and holding a row lock across that would pin a connection for the whole
render.

Preflight runs twice on purpose. The UI calls it to show the researcher what will
be lost, and this service calls it again at the moment of export, because a style
can be changed or a revision accepted in between and the acknowledgements were
collected against the earlier answer.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import DocumentRevision, ExportRun, Paper
from app.domain.document import Document
from app.domain.errors import ExportBlockedError, as_app_error
from app.domain.lifecycle import CitationStyle, RunStatus
from app.observability import get_logger
from app.services.deadline import Deadline
from app.services.exporter import preflight as preflight_module
from app.services.exporter import render_set as render_set_module
from app.services.exporter import renderer
from app.settings import get_settings
from app.storage import exports as export_storage

logger = get_logger(__name__)


def preflight(
    session: Session, paper_id: str, revision_id: str | None = None
) -> preflight_module.Preflight:
    """What this revision can and cannot promise, without exporting anything."""
    paper = repositories.get_paper(session, paper_id)
    revision = (
        repositories.get_revision(session, revision_id)
        if revision_id
        else repositories.get_current_revision(session, paper)
    )
    document = Document.model_validate(revision.document)
    original = _original_reference_ids(session, paper)
    set_ = render_set_module.build(document, original_reference_ids=original)

    return preflight_module.check(
        document,
        revision_id=revision.id,
        citation_style=CitationStyle(paper.citation_style) if paper.citation_style else None,
        retained_uncited_ids=set_.retained_uncited_ids,
        dropped_added_ids=render_set_module.dropped_added_reference_ids(
            document, original_reference_ids=original
        ),
    )


def run_export(
    session: Session,
    paper_id: str,
    *,
    acknowledged_warning_ids: list[str],
) -> ExportRun:
    """Render the paper's current revision into downloadable artifacts."""
    deadline = Deadline.after("export", get_settings().export_deadline_seconds)
    paper = repositories.get_paper(session, paper_id)
    revision = repositories.get_current_revision(session, paper)
    document = Document.model_validate(revision.document)

    checks = preflight(session, paper_id, revision.id)
    if not checks.can_export:
        raise ExportBlockedError(
            "This revision cannot be exported at full fidelity.",
            paper_id=paper_id,
            blockers=[blocker.code for blocker in checks.blockers],
        )

    missing = sorted(set(checks.required_warning_ids) - set(acknowledged_warning_ids))
    if missing:
        raise ExportBlockedError(
            "This export is lossy in ways that must be acknowledged first.",
            paper_id=paper_id,
            missing_warning_ids=missing,
        )

    style = CitationStyle(str(paper.citation_style))
    run = ExportRun(
        id=repositories.new_id("exp"),
        paper_id=paper_id,
        revision_id=revision.id,
        citation_style=style.value,
        status=RunStatus.PENDING.value,
        acknowledged_warning_ids=sorted(set(acknowledged_warning_ids)),
    )
    session.add(run)
    session.commit()

    storage = _storage_dir(paper)

    try:
        staging = export_storage.staging_dir(storage, run.id)
        set_ = render_set_module.build(
            document, original_reference_ids=_original_reference_ids(session, paper)
        )
        artifacts = renderer.render(
            document,
            set_,
            style=style,
            into=staging,
            deadline=deadline,
            asset_root=storage,
        )

        export_storage.publish(storage, run.id)
        run.status = RunStatus.COMPLETED.value
        run.artifacts = [
            {
                "name": artifact.name,
                "media_type": artifact.media_type,
                "size_bytes": artifact.size_bytes,
                "href": f"/exports/{run.id}/artifacts/{artifact.name}",
            }
            for artifact in artifacts
        ]
        session.commit()
    except Exception as error:
        code = as_app_error(error).code
        session.rollback()
        export_storage.discard(storage, run.id)
        run = repositories.get_export_run(session, run.id)
        run.status = RunStatus.FAILED.value
        run.failure_code = code.value
        run.failure_detail = "This export could not be produced."
        session.commit()
        logger.warning(
            "export.failed",
            extra={"paper_id": paper_id, "run_id": run.id, "code": code.value},
        )
        raise
    logger.info(
        "export.completed",
        extra={
            "paper_id": paper_id,
            "run_id": run.id,
            "style": style.value,
            "retained_uncited": len(set_.retained_uncited_ids),
        },
    )
    return run


def artifact_path(session: Session, run_id: str, name: str) -> Path:
    run = repositories.get_export_run(session, run_id)
    paper = repositories.get_paper(session, run.paper_id)
    return export_storage.artifact_path(_storage_dir(paper), run.id, name)


def _storage_dir(paper: Paper) -> Path:
    return get_settings().papers_dir / paper.storage_id


def _original_reference_ids(session: Session, paper: Paper) -> frozenset[str]:
    """The bibliography as uploaded, from revision 1.

    Retention policy is about what the *author* provided, so it is read from the
    parse rather than from the revision being exported: a reference this system
    added and later dropped was never the author's, and printing it would be a
    claim they did not make.
    """
    first = (
        session.query(DocumentRevision)
        .filter_by(paper_id=paper.id, revision_number=1)
        .one_or_none()
    )
    if first is None:
        return frozenset()
    return frozenset(
        reference.id for reference in Document.model_validate(first.document).references
    )
