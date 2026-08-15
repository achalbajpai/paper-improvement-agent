"""Snapshotting provider records into ``source_records``.

Every piece of provider text this system shows a researcher, or bases a verdict
on, is read from a row written here -- never from a live provider response at
display time. Evidence anchors address a span of an abstract by character
offsets, and a provider quietly editing an abstract would move every offset so
that the quoted sentence drifts away from the claim it was supposed to support.

Snapshots are deduplicated per paper on ``(provider, external_id)``, so a work
cited twice is stored once and both citations point at the same evidence. That
is a unique constraint rather than only a read-then-write check: two operations
on one paper run concurrently, and two snapshots of one work would split its
evidence so that findings quoting the same abstract cited different rows.

Each snapshot is its own short transaction. Callers snapshot a work and then
make a model call about it, so a snapshot that left its transaction open would
hold a pooled connection across every subsequent external call in the
operation.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import RetrievalAttempt, SourceRecord
from app.db.repositories import new_id
from app.domain.source import ProviderWork, SourceRecordSnapshot
from app.providers.session import ProviderSession


class SourceStore:
    """Per-paper snapshot store. One instance per operation."""

    def __init__(self, session: Session, paper_id: str) -> None:
        self.session = session
        self.paper_id = paper_id
        self._by_external: dict[tuple[str, str], str] = {}

    def snapshot(self, work: ProviderWork) -> str:
        """Store this work if it is new, and return its source record id.

        An existing snapshot is returned unchanged rather than refreshed. That is
        the point: a review's evidence must keep meaning what it meant when the
        verdict was made.

        Committed here rather than left to the caller's final write. Reading or
        writing opens a transaction, and the caller's next act is a model call
        that can take tens of seconds -- so leaving it open would pin a pooled
        connection for the length of the review, which is exactly what the
        operation's phase structure exists to avoid. A snapshot is immutable
        evidence scoped to the paper, so committing one early is safe even if
        the operation later fails: it cascades away with the paper, and a
        subsequent run reuses it rather than re-fetching.
        """
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
    """Write this operation's provider call log.

    Queries are manuscript-derived, so they are written to a paper-scoped row
    that cascades on delete, and never to a log line.
    """
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
