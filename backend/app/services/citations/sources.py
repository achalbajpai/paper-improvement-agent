from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import RetrievalAttempt, SourceRecord
from app.db.repositories import new_id
from app.domain.source import ProviderWork, SourceRecordSnapshot
from app.providers.session import ProviderSession


class SourceStore:
    def __init__(self, session: Session, paper_id: str) -> None:
        self.session = session
        self.paper_id = paper_id
        self._by_external: dict[tuple[str, str], str] = {}

    def snapshot(self, work: ProviderWork) -> str:
        key = (work.provider.value, work.external_id)
        if key in self._by_external:
            return self._by_external[key]

        existing = self.session.execute(
            select(SourceRecord).where(
                SourceRecord.paper_id == self.paper_id,
                SourceRecord.provider == work.provider.value,
                SourceRecord.external_id == work.external_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.abstract is None and work.has_abstract:
                existing.abstract = work.abstract
            self.session.commit()
            self._by_external[key] = existing.id
            return existing.id

        snapshot = SourceRecordSnapshot.of(new_id("src"), work)
        try:
            with self.session.begin_nested():
                self.session.add(
                    SourceRecord(
                        id=snapshot.id,
                        paper_id=self.paper_id,
                        provider=snapshot.provider.value,
                        external_id=snapshot.external_id,
                        doi=snapshot.doi,
                        title=snapshot.title,
                        abstract=snapshot.abstract,
                        url=snapshot.url,
                        csl=snapshot.csl,
                        raw_payload=snapshot.raw_payload,
                    )
                )
        except IntegrityError:
            winner = self.session.execute(
                select(SourceRecord).where(
                    SourceRecord.paper_id == self.paper_id,
                    SourceRecord.provider == work.provider.value,
                    SourceRecord.external_id == work.external_id,
                )
            ).scalar_one()
            self.session.commit()
            self._by_external[key] = winner.id
            return winner.id

        self.session.commit()
        self._by_external[key] = snapshot.id
        return snapshot.id

    def get(self, record_id: str) -> SourceRecord | None:
        record = self.session.get(SourceRecord, record_id)
        return record if record is not None and record.paper_id == self.paper_id else None


def record_attempts(session: Session, paper_id: str, provider_session: ProviderSession) -> None:
    for attempt in provider_session.attempts:
        session.add(
            RetrievalAttempt(
                id=new_id("att"),
                paper_id=paper_id,
                operation_id=provider_session.operation_id,
                provider=attempt.provider.value,
                purpose=attempt.purpose.value,
                query=attempt.query,
                status=attempt.status,
                failure_code=attempt.failure_code,
                result_count=attempt.result_count,
                latency_ms=attempt.latency_ms,
            )
        )
    provider_session.attempts.clear()
    session.flush()
