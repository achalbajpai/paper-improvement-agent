from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db.models import Paper, SourceRecord
from app.db.repositories import new_id
from app.domain.citation import CitationItem, CitationNode
from app.domain.document import CitationRef, Document, Paragraph, Section, TextRun
from app.domain.reference import CSLItem, ReferenceRecord
from app.domain.verification import BlockerCode, CheckName, CheckStatus
from app.providers.llm import Prompt
from app.services.deadline import Deadline
from app.services.editor.candidate_revision import CandidateRevisionBuilder
from app.services.editor.delta_engine import compute_delta
from app.services.editor.verification import VerificationInputs, verify
from tests.support.llm import ScriptedLLM

CLUSTER = "cite_imported_001"

ABSTRACTS = {
    "src_a": "Work A reports a fifteen percent gain. It was measured on one corpus.",
    "src_b": "Work B reports no gain at all. The effect vanished under replication.",
}


def manuscript() -> Document:
    return Document(
        title="A paper",
        sections=(
            Section(
                id="sec_1",
                title="Introduction",
                body=(
                    Paragraph(
                        id="p_1_1",
                        order_index=0,
                        inlines=(
                            TextRun(text="The method improves accuracy substantially "),
                            CitationRef(citation_id=CLUSTER),
                            TextRun(text="."),
                        ),
                    ),
                ),
            ),
        ),
        citations={
            CLUSTER: CitationNode(
                id=CLUSTER,
                raw_marker="[1, 2]",
                items=(
                    CitationItem(reference_id="ref_imported_001"),
                    CitationItem(reference_id="ref_imported_002"),
                ),
            )
        },
        references=(
            _reference("ref_imported_001", "Work A", "src_a"),
            _reference("ref_imported_002", "Work B", "src_b"),
        ),
        segmenter_version="seg/v1",
    )


def _reference(reference_id: str, title: str, record_id: str) -> ReferenceRecord:
    return ReferenceRecord(
        id=reference_id,
        csl=CSLItem(id=reference_id, title=title),
        source_record_id=record_id,
    )


def reworded(document: Document) -> Document:
    builder = CandidateRevisionBuilder(document)
    builder.replace_paragraph(
        "p_1_1", f"The method eliminates the error entirely [[CITE:{CLUSTER}]]."
    )
    return builder.build()


def snapshots(db: Session, paper_id: str, *, ids: tuple[str, ...] = ("src_a", "src_b")) -> None:
    for record_id in ids:
        db.add(
            SourceRecord(
                id=record_id,
                paper_id=paper_id,
                provider="OPENALEX",
                external_id=f"W_{record_id}",
                title=record_id,
                abstract=ABSTRACTS[record_id],
                csl={},
                raw_payload={},
            )
        )
    db.flush()


def scripted(verdicts: dict[str, str]) -> ScriptedLLM:

    def support(prompt: Prompt) -> dict[str, Any]:
        for record_id, abstract in ABSTRACTS.items():
            if abstract.split(".")[0] in prompt.user:
                return {
                    "verdict": verdicts[record_id],
                    "evidence_span_ids": ["span_000"],
                    "reason": "as scripted",
                }
        raise AssertionError("the prompt quoted no known abstract")

    return ScriptedLLM({"support": support})


def run(db: Session, paper_id: str, verdicts: dict[str, str]) -> Any:
    base = manuscript()
    candidate = reworded(base)
    return verify(
        VerificationInputs(
            paper_id=paper_id,
            base=base,
            candidate=candidate,
            delta=compute_delta(base, candidate),
        ),
        session=db,
        llm=scripted(verdicts),
        deadline=Deadline.after("test", 60.0),
    )


def attachment(result: Any) -> Any:
    return next(check for check in result.checks if check.name is CheckName.SEMANTIC_ATTACHMENT)


def contradicted(result: Any) -> bool:
    return any(blocker.code is BlockerCode.ATTACHMENT_CONTRADICTED for blocker in result.blockers)


@pytest.mark.parametrize(
    ("verdicts", "expected"),
    [
        ({"src_a": "SUPPORTED", "src_b": "CONTRADICTED"}, True),
        ({"src_a": "CONTRADICTED", "src_b": "SUPPORTED"}, True),
        ({"src_a": "CONTRADICTED", "src_b": "CONTRADICTED"}, True),
        ({"src_a": "SUPPORTED", "src_b": "SUPPORTED"}, False),
    ],
    ids=["second-contradicts", "first-contradicts", "both-contradict", "both-support"],
)
def test_a_cluster_is_blocked_if_any_reference_contradicts(
    db: Session, stored_paper: Paper, verdicts: dict[str, str], expected: bool
) -> None:
    snapshots(db, stored_paper.id)

    result = run(db, stored_paper.id, verdicts)

    assert contradicted(result) is expected
    assert (attachment(result).status is CheckStatus.BLOCKED) is expected


def test_every_reference_in_the_cluster_is_actually_consulted(
    db: Session, stored_paper: Paper
) -> None:
    snapshots(db, stored_paper.id)
    seen: list[str] = []

    def support(prompt: Prompt) -> dict[str, Any]:
        for record_id, abstract in ABSTRACTS.items():
            if abstract.split(".")[0] in prompt.user:
                seen.append(record_id)
        return {"verdict": "SUPPORTED", "evidence_span_ids": ["span_000"], "reason": "r"}

    base = manuscript()
    candidate = reworded(base)
    verify(
        VerificationInputs(
            paper_id=stored_paper.id,
            base=base,
            candidate=candidate,
            delta=compute_delta(base, candidate),
        ),
        session=db,
        llm=ScriptedLLM({"support": support}),
        deadline=Deadline.after("test", 60.0),
    )

    assert sorted(seen) == ["src_a", "src_b"]


def test_a_reference_with_no_snapshot_does_not_pass_as_supported(
    db: Session, stored_paper: Paper
) -> None:
    snapshots(db, stored_paper.id, ids=("src_a",))

    result = run(db, stored_paper.id, {"src_a": "SUPPORTED", "src_b": "SUPPORTED"})

    assert attachment(result).status is not CheckStatus.PASSED
    assert not contradicted(result)


def test_a_snapshot_belonging_to_another_paper_is_never_used(
    db: Session, stored_paper: Paper
) -> None:
    other = Paper(
        id=new_id("paper"),
        storage_id=new_id("store"),
        original_filename="other.pdf",
        content_sha256="9" * 64,
        size_bytes=1,
        status="PARSED",
    )
    db.add(other)
    db.flush()
    snapshots(db, other.id)

    result = run(db, stored_paper.id, {"src_a": "SUPPORTED", "src_b": "SUPPORTED"})

    assert attachment(result).status is not CheckStatus.PASSED
    assert db.query(SourceRecord).filter(SourceRecord.paper_id == stored_paper.id).count() == 0
