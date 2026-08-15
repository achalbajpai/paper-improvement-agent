from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.db.models import DocumentRevision, Paper
from app.db.repositories import new_id
from app.db.session import check_database, get_engine, init_db
from app.domain.lifecycle import PaperStatus
from app.services.parser.postvalidator import ValidatedDocument, postvalidate
from app.services.parser.tei_mapper import MappedDocument, map_tei
from app.services.parser.xml_safety import parse_tei

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TEI_DIR = FIXTURES / "tei"

CORPUS = ("A_numeric", "B_author_year", "C_numeric_dense")


def load_tei(name: str) -> bytes:
    return (TEI_DIR / f"{name}.tei.xml").read_bytes()


def mapped(name: str) -> MappedDocument:
    return map_tei(parse_tei(load_tei(name)))


def validated(name: str) -> ValidatedDocument:
    return postvalidate(mapped(name))


@pytest.fixture(scope="session")
def manifest() -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / "manifest.json").read_text())
    return data


@pytest.fixture(params=CORPUS)
def corpus_name(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    if not check_database():
        pytest.skip("Postgres is not reachable; database tests run inside the api container")
    init_db()
    yield


@pytest.fixture
def db(database: None) -> Iterator[Session]:
    connection = get_engine().connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def stored_paper(db: Session) -> Paper:
    document = validated("A_numeric").document
    paper = Paper(
        id=new_id("paper"),
        storage_id=new_id("store"),
        original_filename="A_numeric.pdf",
        content_sha256="0" * 64,
        size_bytes=1024,
        status=PaperStatus.PARSED.value,
        title=document.title or None,
    )
    db.add(paper)
    db.flush()
    revision = DocumentRevision(
        id=new_id("rev"),
        paper_id=paper.id,
        revision_number=1,
        document=document.model_dump(mode="json"),
        content_sha256=document.content_hash(),
        segmenter_version=document.segmenter_version,
    )
    db.add(revision)
    paper.current_revision_id = revision.id
    db.flush()
    return paper
