from __future__ import annotations

from fastapi import status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import OperationRequest


def complete_operation(
    session: Session,
    operation_id: str,
    body: BaseModel,
    *,
    status_code: int = status.HTTP_200_OK,
) -> None:
    claim = session.get(OperationRequest, operation_id)
    if claim is None:
        return
    repositories.complete_operation(session, claim, status_code, body.model_dump(mode="json"))
    session.commit()


def release_operation(
    session: Session,
    operation_id: str,
    *,
    rollback: bool = False,
) -> None:
    if rollback:
        session.rollback()
    claim = session.get(OperationRequest, operation_id)
    if claim is None:
        return
    repositories.fail_operation(session, claim)
    session.commit()
