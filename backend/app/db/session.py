from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.settings import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=5,
        future=True,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    importlib.import_module("app.db.models")
    Base.metadata.create_all(bind=get_engine())
    _apply_post_create_constraints()


def _apply_post_create_constraints() -> None:
    statements_by_table = {
        "operation_requests": (
            """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_operation_requests_scope_key
                ON operation_requests (scope_type, scope_id, operation_type, idempotency_key)
                NULLS NOT DISTINCT
            """,
        ),
        "edit_proposals": (
            """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conname = 'fk_edit_proposal_base_revision'
                    ) THEN
                        ALTER TABLE edit_proposals
                        ADD CONSTRAINT fk_edit_proposal_base_revision
                        FOREIGN KEY (paper_id, base_revision_id)
                        REFERENCES document_revisions (paper_id, id)
                        ON DELETE CASCADE;
                    END IF;
                END $$
            """,
        ),
    }
    engine = get_engine()
    present = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        for table, statements in statements_by_table.items():
            if table in present:
                for statement in statements:
                    connection.execute(text(statement))


def check_database() -> bool:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True
