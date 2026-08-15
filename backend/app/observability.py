"""Logging setup.

Manuscript prose never appears in logs, traces, exception messages, or
analytics. Retrieval queries are manuscript-derived and are therefore treated
the same way: telemetry carries request id, provider, latency, result count, and
status, and the query text is persisted only in the paper-scoped
``retrieval_attempts`` row that cascades on delete.
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


_BUILTIN_RECORD_FIELDS = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "message",
    "asctime",
    "request_id",
    "taskName",
}


class KeyValueFormatter(logging.Formatter):
    """Render the ``extra`` fields.

    Call sites pass paper id, run id, error code, the identifier a model returned;
    a ``%(message)s`` format string dropped all of it. The privacy rule is enforced
    at the call sites, so this adds context, not content.
    """

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _BUILTIN_RECORD_FIELDS and not key.startswith("_")
        }
        if not fields:
            return base
        rendered = " ".join(f"{key}={value!r}" for key, value in sorted(fields.items()))
        return f"{base} {rendered}"


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        KeyValueFormatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s")
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
