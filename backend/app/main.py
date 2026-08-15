"""FastAPI application and database startup."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from app.api import exports, papers, proposals, reviews
from app.api.schemas import ErrorEnvelope
from app.db.models import Paper
from app.db.session import check_database, init_db, session_scope
from app.domain.errors import AppError, ErrorCode
from app.observability import configure_logging, get_logger, new_request_id, request_id_var
from app.providers.grobid import GrobidClient
from app.services.recovery import reconcile_interrupted
from app.settings import get_settings
from app.storage.manuscripts import quarantine_orphaned_papers, sweep_trash

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    settings.papers_dir.mkdir(parents=True, exist_ok=True)
    settings.trash_dir.mkdir(parents=True, exist_ok=True)
    init_db()

    with session_scope() as session:
        reconcile_interrupted(session)
        known_storage_ids = {value for (value,) in session.query(Paper.storage_id).all()}

    swept = sweep_trash()
    if swept:
        logger.info("swept %d orphaned trash directories", swept)
    quarantined = quarantine_orphaned_papers(known_storage_ids)
    if quarantined:
        logger.warning("quarantined %d unreferenced paper directories", quarantined)
    yield


class PublicFastAPI(FastAPI):
    def openapi(self) -> dict[str, Any]:
        """Describe the validation envelope the application actually returns."""
        if self.openapi_schema is not None:
            return self.openapi_schema
        document = get_openapi(
            title=self.title,
            version=self.version,
            routes=self.routes,
            description=self.description,
        )
        for path_item in document.get("paths", {}).values():
            if not isinstance(path_item, dict):
                continue
            for operation in path_item.values():
                if not isinstance(operation, dict):
                    continue
                responses = operation.get("responses")
                if isinstance(responses, dict):
                    responses.pop("422", None)
        self.openapi_schema = document
        return document


app = PublicFastAPI(
    title="Paper Improvement Agent",
    version="0.1.0",
    lifespan=lifespan,
    separate_input_output_schemas=False,
    responses={400: {"model": ErrorEnvelope, "description": "Malformed request"}},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def attach_request_id(request: Request, call_next: Any) -> Any:
    token = request_id_var.set(new_request_id())
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id_var.get()
        return response
    finally:
        request_id_var.reset(token)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:

    logger.warning("%s on %s: %s", exc.code.value, request.url.path, exc.message)
    return JSONResponse(status_code=exc.http_status, content=exc.envelope())


@app.exception_handler(RequestValidationError)
async def handle_request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Keep framework validation inside the public error contract.

    Validation details can contain uploaded filenames or request text, so the
    response and logs report only the failing field locations and never echo
    the rejected input.
    """
    locations = [".".join(str(part) for part in error["loc"]) for error in exc.errors()]
    logger.warning("malformed request on %s: %s", request.url.path, locations)
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": ErrorCode.MALFORMED_REQUEST.value,
                "message": "The request does not match the API contract.",
                "details": {"fields": locations[:20]},
            }
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": ErrorCode.INTERNAL_ERROR.value,
                "message": "An unexpected error occurred.",
                "details": {},
            }
        },
    )


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
def ready(response: Response) -> dict[str, object]:
    """What this instance can actually do right now.

    ``status`` is derived rather than asserted. A readiness probe that returns
    ``ok`` with a dead database is not a probe, and Compose is configured to
    gate on this endpoint -- so a hard-coded ``ok`` would report every instance
    healthy including the ones that are not.

    The dependencies are reported separately because they fail separately, and
    each disables a different part of the product: without Pandoc nothing
    exports, without GROBID nothing parses, without a model there is no review
    or editing. The UI reads these to disable what cannot work rather than
    letting a researcher discover it by clicking.
    """
    settings = get_settings()
    checks = {
        "database": check_database(),
        "grobid": GrobidClient().is_alive(),
        "pandoc": shutil.which("pandoc") is not None,
    }
    if not all(checks.values()):
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    scholarly_keyed = bool(settings.openalex_api_key and settings.semantic_scholar_api_key)
    scholarly_warnings: list[str] = []
    if not settings.openalex_api_key:
        scholarly_warnings.append(
            "OpenAlex has no API key. Its current API requires one, so OpenAlex coverage "
            "will be unavailable."
        )
    if not settings.semantic_scholar_api_key:
        scholarly_warnings.append(
            "Semantic Scholar has no API key. Its unauthenticated shared quota is not "
            "reliable enough for complete review coverage."
        )

    return {
        "status": "ok" if all(checks.values()) else "degraded",
        **checks,
        "scholarly_search_keyed": scholarly_keyed,
        "warnings": scholarly_warnings,
        "llm_configured": settings.llm_configured,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "llm_fallback_configured": bool(
            settings.llm_fallback_provider
            and settings.llm_fallback_api_key
            and settings.llm_fallback_model
            and settings.llm_fallback_base_url
        ),
        "openalex_keyed": bool(settings.openalex_api_key),
        "semantic_scholar_keyed": bool(settings.semantic_scholar_api_key),
    }


app.include_router(papers.router)
app.include_router(reviews.router)
app.include_router(proposals.router)
app.include_router(exports.router)
