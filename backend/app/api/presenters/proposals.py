from __future__ import annotations

from sqlalchemy.orm import Session

from app.api.presenters.shared import _error_code
from app.api.schemas import (
    BlockerOut,
    ChangeOut,
    CheckOut,
    DeltaOut,
    EditScopeOut,
    ParagraphDiff,
    ProposalOut,
    SkippedParagraphOut,
    WarningOut,
)
from app.db.models import DocumentRevision, EditProposal
from app.domain.delta import ComputedEditDelta
from app.domain.document import Document
from app.domain.proposal import CandidateRevisionSnapshot, EditScope, ProposalState


def proposal(session: Session, row: EditProposal) -> ProposalOut:
    body = ProposalOut(
        id=row.id,
        paper_id=row.paper_id,
        base_revision_id=row.base_revision_id,
        command=row.command,
        intent=row.intent,
        state=ProposalState(row.state),
        failure_code=_error_code(row.failure_code),
        failure_detail=row.failure_detail,
        candidate_sha256=row.candidate_sha256,
        required_warning_ids=[str(value) for value in row.required_warning_ids],
    )
    if row.candidate is None:
        return body

    snapshot = CandidateRevisionSnapshot.model_validate(row.candidate)
    base = session.get(DocumentRevision, row.base_revision_id)
    before = Document.model_validate(base.document) if base else None
    verification = snapshot.verification

    body.delta = _delta(snapshot.delta)
    body.checks = [
        CheckOut(
            name=check.name,
            status=check.status,
            detail=check.detail,
            prompt_version=check.prompt_version,
            model=check.model,
            model_provider=check.provider,
        )
        for check in verification.checks
    ]
    body.blockers = [
        BlockerOut(
            code=blocker.code, message=blocker.message, subject_ids=list(blocker.subject_ids)
        )
        for blocker in verification.blockers
    ]
    body.warnings = [
        WarningOut(
            id=warning.id,
            code=warning.code,
            message=warning.message,
            subject_ids=list(warning.subject_ids),
        )
        for warning in verification.warnings
    ]
    body.explanation = snapshot.explanation
    body.scope = _scope(snapshot.scope)
    body.diffs = _diffs(before, snapshot.document, snapshot.delta.changed_paragraph_ids)
    return body


def _delta(delta: ComputedEditDelta) -> DeltaOut:
    return DeltaOut(
        changes=[
            ChangeOut(
                kind=change.kind.value,
                subject_id=change.subject_id,
                related_ids=list(change.related_ids),
                detail=change.detail,
            )
            for change in delta.changes
        ],
        words_before=delta.words_before,
        words_after=delta.words_after,
        scope_words_before=delta.scope_words_before,
        scope_words_after=delta.scope_words_after,
        summary=delta.summary(),
    )


def _scope(scope: EditScope | None) -> EditScopeOut | None:
    if scope is None:
        return None
    return EditScopeOut(
        section_id=scope.section_id,
        section_title=scope.section_title,
        paragraph_id=scope.paragraph_id,
        targeted_paragraph_ids=list(scope.targeted_paragraph_ids),
        skipped=[
            SkippedParagraphOut(paragraph_id=item.paragraph_id, reason=item.reason)
            for item in scope.skipped
        ],
        words_before=scope.words_before,
        words_target=scope.words_target,
        section_paragraph_count=scope.section_paragraph_count,
    )


def _diffs(
    before: Document | None, after: Document, paragraph_ids: tuple[str, ...]
) -> list[ParagraphDiff]:
    diffs: list[ParagraphDiff] = []
    for paragraph_id in paragraph_ids:
        revised = after.paragraph(paragraph_id)
        if revised is None:
            continue
        original = before.paragraph(paragraph_id) if before else None
        section = after.section_of(paragraph_id)
        diffs.append(
            ParagraphDiff(
                paragraph_id=paragraph_id,
                section_id=section.id if section else None,
                before_text=original.text if original else "",
                after_text=revised.text,
                before_citation_ids=list(original.citation_ids) if original else [],
                after_citation_ids=list(revised.citation_ids),
            )
        )
    return diffs
