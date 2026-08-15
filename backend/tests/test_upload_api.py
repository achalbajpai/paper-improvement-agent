from __future__ import annotations

from io import BytesIO
from typing import Any

import pytest
from fastapi import Response, UploadFile

from app.api.papers import upload_paper
from app.domain.errors import FileTooLargeError, UnsupportedFileTypeError
from app.settings import get_settings


def _must_not_claim(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("an invalid upload claimed an operation")


def test_oversized_upload_is_bounded_and_rejected_before_claim(monkeypatch: Any) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 5)
    monkeypatch.setattr("app.api.papers.repositories.begin_operation", _must_not_claim)
    upload = UploadFile(filename="large.pdf", file=BytesIO(b"%PDF-x"))

    with pytest.raises(FileTooLargeError):
        upload_paper(None, "idem", Response(), upload)


def test_non_pdf_is_rejected_before_claim(monkeypatch: Any) -> None:
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 100)
    monkeypatch.setattr("app.api.papers.repositories.begin_operation", _must_not_claim)
    upload = UploadFile(filename="not.pdf", file=BytesIO(b"plain text"))

    with pytest.raises(UnsupportedFileTypeError):
        upload_paper(None, "idem", Response(), upload)
