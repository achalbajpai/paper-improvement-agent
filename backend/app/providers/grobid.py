from __future__ import annotations

import httpx

from app.domain.errors import ParserFailedError, ParserUnavailableError
from app.observability import get_logger
from app.settings import get_settings

logger = get_logger(__name__)


FULLTEXT_PARAMS: dict[str, str | list[str]] = {
    "consolidateHeader": "0",
    "consolidateCitations": "0",
    "includeRawCitations": "1",
    "includeRawAffiliations": "0",
    "segmentSentences": "0",
    "teiCoordinates": ["figure", "formula", "title", "persName", "p", "biblStruct"],
}


class GrobidClient:
    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.grobid_url).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.grobid_timeout_seconds

    def is_alive(self) -> bool:
        try:
            response = httpx.get(f"{self.base_url}/api/isalive", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200 and response.text.strip() == "true"

    def health(self) -> dict[str, object]:
        try:
            response = httpx.get(f"{self.base_url}/api/health", timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ParserUnavailableError("GROBID is not reachable.") from exc
        data: dict[str, object] = response.json()
        return data

    def process_fulltext(self, pdf_bytes: bytes, timeout: float | None = None) -> str:
        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            response = httpx.post(
                f"{self.base_url}/api/processFulltextDocument",
                files={"input": ("upload.pdf", pdf_bytes, "application/pdf")},
                data=FULLTEXT_PARAMS,
                timeout=effective_timeout,
            )
        except httpx.TimeoutException as exc:
            raise ParserUnavailableError(
                "GROBID did not respond within the remaining budget.",
                timeout_seconds=effective_timeout,
            ) from exc
        except httpx.HTTPError as exc:
            raise ParserUnavailableError("GROBID is not reachable.") from exc

        if response.status_code == 204:
            raise ParserFailedError("GROBID extracted no content from this PDF.")
        if response.status_code == 503:
            raise ParserUnavailableError("GROBID has no worker available.")
        if response.status_code != 200:
            raise ParserFailedError(
                "GROBID rejected the document.", status_code=response.status_code
            )
        return response.text
