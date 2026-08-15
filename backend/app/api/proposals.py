"""HTTP translation for editing proposals and their acceptance."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.api.deps import IdempotencyKeyDep, SessionDep
from app.api.operations import complete_operation, release_operation
from app.api.presenters import proposal as present_proposal
from app.api.schemas import (
    AcceptProposalRequest,
    AcceptProposalResponse,
    CreateProposalRequest,
    ProposalOut,
)
from app.db import repositories
from app.domain.errors import CandidateSnapshotError
from app.services.editor import acceptance, proposals

router = APIRouter(tags=["proposals"])


@router.post("/papers/{paper_id}/proposals", response_model=ProposalOut)
def create_proposal(
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    paper_id: str,
    body: CreateProposalRequest,
) -> ProposalOut:
    outcome = repositories.begin_operation(
        session,
        scope_type="paper",
        scope_id=paper_id,
        operation_type="CREATE_PROPOSAL",
        idempotency_key=idempotency_key,
        request_body={
            "paper_id": paper_id,
            "command": body.command,
            "section_id": body.section_id,
            "paragraph_id": body.paragraph_id,
        },
    )
    if isinstance(outcome, repositories.ReplayedResponse):
        response.status_code = outcome.status_code
        return ProposalOut.model_validate(outcome.body)

    operation_id = outcome.id
    session.commit()
    try:
        row = proposals.create_proposal(
            session,
            paper_id,
            body.command,
            operation_id=operation_id,
            target_section_id=body.section_id,
            target_paragraph_id=body.paragraph_id,
        )
    except Exception:
        release_operation(session, operation_id)
        raise

    presented = present_proposal(session, row)
    complete_operation(session, operation_id, presented)
    return presented


@router.get("/papers/{paper_id}/proposals", response_model=list[ProposalOut])
def list_proposals(session: SessionDep, paper_id: str) -> list[ProposalOut]:
    repositories.get_paper(session, paper_id)
    return [
        present_proposal(session, row) for row in repositories.list_proposals(session, paper_id)
    ]


@router.get("/proposals/{proposal_id}", response_model=ProposalOut)
def get_proposal(session: SessionDep, proposal_id: str) -> ProposalOut:
    return present_proposal(session, repositories.get_proposal(session, proposal_id))


@router.post("/proposals/{proposal_id}/accept", response_model=AcceptProposalResponse)
def accept(
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    proposal_id: str,
    body: AcceptProposalRequest,
) -> AcceptProposalResponse:
    outcome = repositories.begin_operation(
        session,
        scope_type="proposal",
        scope_id=proposal_id,
        operation_type="ACCEPT_PROPOSAL",
        idempotency_key=idempotency_key,
        request_body={
            "proposal_id": proposal_id,
            "candidate_sha256": body.candidate_sha256,
            "acknowledged_warning_ids": sorted(body.acknowledged_warning_ids),
        },
    )
    if isinstance(outcome, repositories.ReplayedResponse):
        response.status_code = outcome.status_code
        return AcceptProposalResponse.model_validate(outcome.body)

    operation_id = outcome.id
    session.commit()

    stored = repositories.get_proposal(session, proposal_id)
    if stored.candidate_sha256 != body.candidate_sha256:
        release_operation(session, operation_id)
        raise CandidateSnapshotError(
            "This proposal has changed since it was displayed. Review it again before accepting.",
            proposal_id=proposal_id,
        )

    try:
        revision = acceptance.accept_proposal(
            session, proposal_id, acknowledged_warning_ids=body.acknowledged_warning_ids
        )
    except Exception:
        release_operation(session, operation_id)
        raise

    result = AcceptProposalResponse(
        proposal_id=proposal_id,
        revision_id=revision.id,
        revision_number=revision.revision_number,
    )
    complete_operation(session, operation_id, result)
    return result


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalOut)
def reject(
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    response: Response,
    proposal_id: str,
) -> ProposalOut:
    """Rejection is a state transition, and it is claimed like every other one.

    Without a claim, a lost response followed by the client's retry answers
    with a state conflict rather than replaying the success it never saw --
    which reads to the researcher as though the rejection failed.
    """
    outcome = repositories.begin_operation(
        session,
        scope_type="proposal",
        scope_id=proposal_id,
        operation_type="REJECT_PROPOSAL",
        idempotency_key=idempotency_key,
        request_body={"proposal_id": proposal_id},
    )
    if isinstance(outcome, repositories.ReplayedResponse):
        response.status_code = outcome.status_code
        return ProposalOut.model_validate(outcome.body)

    operation_id = outcome.id
    session.commit()

    try:
        rejected = acceptance.reject_proposal(session, proposal_id)
    except Exception:
        release_operation(session, operation_id)
        raise

    result = present_proposal(session, rejected)
    complete_operation(session, operation_id, result)
    return result
