from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.db.session import get_session_factory
from app.domain.errors import MalformedRequestError


def get_session() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def require_idempotency_key(
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key")],
) -> str:
    if not idempotency_key.strip():
        raise MalformedRequestError("This request requires an Idempotency-Key header.")
    if len(idempotency_key) > 128:
        raise MalformedRequestError("Idempotency-Key is too long.")
    return idempotency_key.strip()


SessionDep = Annotated[Session, Depends(get_session)]
IdempotencyKeyDep = Annotated[str, Depends(require_idempotency_key)]
