"""Regression tests for the GROBID multipart request contract."""

from __future__ import annotations

import httpx
import pytest

from app.providers.grobid import GrobidClient

EXPECTED_COORDINATES = ["figure", "formula", "title", "persName", "p", "biblStruct"]


def multipart_values(body: bytes, boundary: bytes, field: str) -> list[str]:
    """Every value sent under one multipart field name, in order."""
    marker = f'name="{field}"'.encode()
    return [
        part.split(b"\r\n\r\n", 1)[1].rstrip(b"\r\n").decode()
        for part in body.split(b"--" + boundary)
        if marker in part
    ]


def test_process_fulltext_repeats_tei_coordinate_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The encoded body is the contract here, not the parameter literal.

    HTTPX expands a list *value* into a repeated multipart field, but a list of
    ``(name, value)`` pairs is read as a raw byte sequence and raises
    ``TypeError`` while encoding. That is not an ``httpx.HTTPError``, so it
    escapes the adapter's error mapping and surfaces as an unhandled 500 on
    every parse. Asserting on the wire format pins both regressions: the crash,
    and a dict of plain strings that would quietly request one coordinate
    instead of six.
    """
    captured: dict[str, object] = {}

    def fake_post(url: str, **kwargs: object) -> httpx.Response:
        captured["url"] = url
        captured.update(kwargs)
        return httpx.Response(
            200,
            text="<TEI/>",
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr("app.providers.grobid.httpx.post", fake_post)

    assert GrobidClient(base_url="http://grobid").process_fulltext(b"pdf") == "<TEI/>"

    request = httpx.Request(
        "POST",
        captured["url"],
        data=captured["data"],
        files=captured["files"],
    )
    body = request.read()
    boundary = request.headers["content-type"].split("boundary=")[1].encode()

    assert multipart_values(body, boundary, "teiCoordinates") == EXPECTED_COORDINATES
    assert multipart_values(body, boundary, "includeRawCitations") == ["1"]
    assert b'filename="upload.pdf"' in body
