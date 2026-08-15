"""The HTTP boundary has one honest error shape and an accurate schema."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_missing_idempotency_key_uses_the_public_error_envelope() -> None:
    response = TestClient(app).post(
        "/papers",
        files={"file": ("paper.pdf", b"%PDF-minimal", "application/pdf")},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "MALFORMED_REQUEST",
            "message": "The request does not match the API contract.",
            "details": {"fields": ["header.Idempotency-Key"]},
        }
    }


def test_openapi_marks_idempotency_keys_required() -> None:
    document = app.openapi()
    mutating_posts = [
        ("/papers", "post"),
        ("/papers/{paper_id}/parse", "post"),
        ("/papers/{paper_id}/reviews", "post"),
        ("/papers/{paper_id}/proposals", "post"),
        ("/proposals/{proposal_id}/accept", "post"),
        ("/proposals/{proposal_id}/reject", "post"),
        ("/papers/{paper_id}/exports", "post"),
    ]

    for path, method in mutating_posts:
        parameters = document["paths"][path][method]["parameters"]
        header = next(item for item in parameters if item["name"] == "Idempotency-Key")
        assert header["required"] is True, path


def test_openapi_describes_the_runtime_validation_envelope() -> None:
    document = app.openapi()

    for path_item in document["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            assert "422" not in operation["responses"]
            malformed = operation["responses"]["400"]
            schema = malformed["content"]["application/json"]["schema"]
            assert schema == {"$ref": "#/components/schemas/ErrorEnvelope"}


def test_checked_in_openapi_snapshot_matches_the_application() -> None:
    candidates = [
        Path(__file__).resolve().parents[2] / "web/lib/api/openapi.json",
        Path("/repo/web/lib/api/openapi.json"),
    ]
    snapshot = next((path for path in candidates if path.is_file()), None)

    assert snapshot is not None, "the checked-in OpenAPI snapshot is not mounted"
    assert json.loads(snapshot.read_text(encoding="utf-8")) == app.openapi()
