from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _revision_belongs_to_paper(
    name: str, *, column: str = "revision_id"
) -> tuple[ForeignKeyConstraint, ...]:
    return (
        ForeignKeyConstraint(
            ["paper_id", column],
            ["document_revisions.paper_id", "document_revisions.id"],
            name=name,
            ondelete="CASCADE",
        ),
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Paper(Base, TimestampMixin):
    __tablename__ = "papers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)

    storage_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    original_filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(Text)

    title: Mapped[str | None] = mapped_column(Text)

    citation_style: Mapped[str | None] = mapped_column(String(32))
    detected_citation_style: Mapped[str | None] = mapped_column(String(32))
    detected_style_confidence: Mapped[str | None] = mapped_column(String(16))

    current_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "document_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    """Deferred because papers and revisions reference each other: a paper points
    at its current revision, and every revision points at its paper. Checking at
    statement time would make the order of two writes inside one transaction
    decide whether a valid state is accepted."""

    active_parse_op_id: Mapped[str | None] = mapped_column(String(64))

    revisions: Mapped[list[DocumentRevision]] = relationship(
        back_populates="paper",
        cascade="all, delete-orphan",
        passive_deletes=True,
        foreign_keys="DocumentRevision.paper_id",
    )


class DocumentRevision(Base, TimestampMixin):
    __tablename__ = "document_revisions"
    __table_args__ = (
        UniqueConstraint("accepted_proposal_id", name="uq_revision_accepted_proposal"),
        UniqueConstraint("paper_id", "revision_number", name="uq_revision_number_per_paper"),
        UniqueConstraint("paper_id", "id", name="uq_revision_paper"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)

    document: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_revision_id: Mapped[str | None] = mapped_column(
        ForeignKey(
            "document_revisions.id",
            ondelete="SET NULL",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )
    accepted_proposal_id: Mapped[str | None] = mapped_column(
        ForeignKey("edit_proposals.id", ondelete="SET NULL")
    )
    segmenter_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")

    parse_report: Mapped[dict[str, Any] | None] = mapped_column(JSONType)

    paper: Mapped[Paper] = relationship(back_populates="revisions", foreign_keys=[paper_id])


class SourceRecord(Base, TimestampMixin):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("paper_id", "id", name="uq_source_record_paper"),
        UniqueConstraint("paper_id", "provider", "external_id", name="uq_source_record_identity"),
        Index("ix_source_records_external", "provider", "external_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    doi: Mapped[str | None] = mapped_column(String(256))
    title: Mapped[str | None] = mapped_column(Text)

    abstract: Mapped[str | None] = mapped_column(Text)

    url: Mapped[str | None] = mapped_column(Text)
    csl: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ReferenceResolution(Base, TimestampMixin):
    __tablename__ = "reference_resolutions"
    __table_args__ = (UniqueConstraint("paper_id", "reference_id", name="uq_reference_resolution"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    reference_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_id: Mapped[str | None] = mapped_column(String(64), index=True)

    method: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(64))


class RetrievalAttempt(Base, TimestampMixin):
    __tablename__ = "retrieval_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64))
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ReviewRun(Base, TimestampMixin):
    __tablename__ = "review_runs"
    __table_args__ = _revision_belongs_to_paper("fk_review_run_revision")

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    failure_code: Mapped[str | None] = mapped_column(String(64))

    degradations: Mapped[list[Any]] = mapped_column(JSONType, nullable=False, default=list)
    stats: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict)

    findings: Mapped[list[ReviewFindingRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan", passive_deletes=True
    )


class ReviewFindingRow(Base, TimestampMixin):
    __tablename__ = "review_findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    occurrence_id: Mapped[str | None] = mapped_column(String(64))
    reference_id: Mapped[str | None] = mapped_column(String(64))
    verdict: Mapped[str | None] = mapped_column(String(48))
    claim_anchor: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False)
    evidence: Mapped[list[Any]] = mapped_column(JSONType, nullable=False, default=list)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_source_record_ids: Mapped[list[Any]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    suggestion_rationales: Mapped[list[Any]] = mapped_column(JSONType, nullable=False, default=list)

    provider: Mapped[str | None] = mapped_column(String(32))
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(48))

    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[ReviewRun] = relationship(back_populates="findings")


class EditProposal(Base, TimestampMixin):
    __tablename__ = "edit_proposals"
    __table_args__ = (
        *_revision_belongs_to_paper("fk_edit_proposal_base_revision", column="base_revision_id"),
        Index(
            "uq_one_active_proposal_per_paper",
            "paper_id",
            unique=True,
            postgresql_where=text("state IN ('PENDING', 'AWAITING_DECISION')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    base_revision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    command: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str | None] = mapped_column(String(48))
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(Text)

    candidate: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    candidate_sha256: Mapped[str | None] = mapped_column(String(64))

    required_warning_ids: Mapped[list[Any]] = mapped_column(JSONType, nullable=False, default=list)
    acknowledged_warning_ids: Mapped[list[Any]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VerificationCheckRow(Base, TimestampMixin):
    __tablename__ = "verification_checks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    proposal_id: Mapped[str] = mapped_column(
        ForeignKey("edit_proposals.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prompt_version: Mapped[str | None] = mapped_column(String(48))
    model: Mapped[str | None] = mapped_column(String(64))
    provider: Mapped[str | None] = mapped_column(String(32))


class ExportRun(Base, TimestampMixin):
    __tablename__ = "export_runs"
    __table_args__ = _revision_belongs_to_paper("fk_export_run_revision")

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_id: Mapped[str] = mapped_column(String(64), nullable=False)
    citation_style: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    failure_code: Mapped[str | None] = mapped_column(String(64))
    failure_detail: Mapped[str | None] = mapped_column(Text)

    acknowledged_warning_ids: Mapped[list[Any]] = mapped_column(
        JSONType, nullable=False, default=list
    )
    artifacts: Mapped[list[Any]] = mapped_column(JSONType, nullable=False, default=list)


class OperationRequest(Base, TimestampMixin):
    __tablename__ = "operation_requests"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str | None] = mapped_column(String(64))
    operation_type: Mapped[str] = mapped_column(String(48), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)

    request_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="IN_PROGRESS")
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
