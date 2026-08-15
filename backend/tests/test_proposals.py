"""Proposal rows: what gets written when a command succeeds, and when it does not.

The point of these tests is the failure row. A system that only records successful
edits leaves a researcher who typed a command and got nothing back unable to tell
whether it was declined, whether it broke, or whether there was genuinely nothing
to add -- and those three call for three different next actions.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.db import repositories
from app.db.models import EditProposal, Paper
from app.domain.document import Document
from app.domain.errors import GroundingValidationError, LLMUnavailableError
from app.domain.proposal import ProposalState
from app.services.deadline import Deadline
from app.services.editor.intent import EditIntent
from app.services.editor.proposals import create_proposal
from app.services.editor.router import route
from tests.support.llm import ScriptedLLM

pytestmark = pytest.mark.usefixtures("database")


def routing_llm(intent: EditIntent, section_id: str, **rest: Any) -> dict[str, Any]:
    return {"intent": intent.value, "section_id": section_id, **rest}


def section_id_of(db: Session, paper: Paper) -> str:
    revision = repositories.get_current_revision(db, paper)
    document = Document.model_validate(revision.document)
    return max(document.sections, key=lambda section: section.word_count()).id


def test_an_unsupported_command_is_recorded_rather_than_dropped(
    db: Session, stored_paper: Paper
) -> None:
    """Declining is a decision, so it comes back as a row that says so."""
    llm = ScriptedLLM({"intent": {"intent": EditIntent.UNSUPPORTED_INTENT.value}})

    proposal = create_proposal(
        db, stored_paper.id, "Translate the paper", operation_id="op_1", llm=llm
    )

    assert proposal.state == ProposalState.FAILED.value
    assert proposal.failure_code == "UNSUPPORTED_INTENT"
    assert proposal.command == "Translate the paper"


def test_an_outage_is_recorded_and_still_raised(db: Session, stored_paper: Paper) -> None:
    """A model this system could not reach says nothing about the manuscript.

    Answering it with the same "nothing to do" shape as a genuine refusal would
    present an outage as a considered decision.
    """
    llm = ScriptedLLM({})

    with pytest.raises(LLMUnavailableError):
        create_proposal(
            db, stored_paper.id, "Shorten the introduction", operation_id="op_x", llm=llm
        )

    proposal = db.query(EditProposal).filter_by(paper_id=stored_paper.id).one()
    assert proposal.state == ProposalState.FAILED.value
    assert proposal.failure_code == "LLM_UNAVAILABLE"


def test_finding_nothing_leaves_a_failed_row_with_no_candidate(
    db: Session, stored_paper: Paper
) -> None:
    """NO_RESULTS is an answer, and the row says so.

    The absent candidate says there is nothing to review; the code says the search
    ran and came back empty, as distinct from a provider that could not be
    reached. Neither fact is inferable from the other, so both are stored.
    """
    section = section_id_of(db, stored_paper)

    llm = ScriptedLLM(
        {
            "intent": routing_llm(EditIntent.ADD_SUPPORTING_CITATIONS, section),
            "claims": {"claims": []},
        }
    )

    proposal = create_proposal(
        db, stored_paper.id, "Add citations here", operation_id="op_2", llm=llm
    )

    assert proposal.state == ProposalState.FAILED.value
    assert proposal.failure_code == "NO_RESULTS"
    assert proposal.candidate is None
    assert proposal.candidate_sha256 is None
    assert proposal.intent == EditIntent.ADD_SUPPORTING_CITATIONS.value


def test_a_failed_proposal_does_not_block_the_next_command(
    db: Session, stored_paper: Paper
) -> None:
    """FAILED is terminal, so it is not an edit awaiting a decision."""
    section = section_id_of(db, stored_paper)
    llm = ScriptedLLM(
        {
            "intent": routing_llm(EditIntent.ADD_SUPPORTING_CITATIONS, section),
            "claims": {"claims": []},
        }
    )

    for index in range(2):
        create_proposal(db, stored_paper.id, "Add citations", operation_id=f"op_r{index}", llm=llm)

    assert db.query(EditProposal).filter_by(paper_id=stored_paper.id).count() == 2


def test_an_ambiguous_command_comes_back_with_the_question_it_raised(
    db: Session, stored_paper: Paper
) -> None:
    """The router works out what to ask, so the researcher should be asked it.

    The clarification used to be computed, packed into the error's details, and
    then dropped in favour of the generic message -- leaving "shorten it by 20%"
    answered by a sentence that names no section at all.
    """
    llm = ScriptedLLM(
        {
            "intent": {
                "intent": EditIntent.AMBIGUOUS_INTENT.value,
                "clarification": "Did you mean the Introduction or Related Work?",
            }
        }
    )

    proposal = create_proposal(
        db, stored_paper.id, "shorten it by 20%", operation_id="op_a", llm=llm
    )

    assert proposal.state == ProposalState.FAILED.value
    assert proposal.failure_code == "AMBIGUOUS_INTENT"
    assert proposal.failure_detail == "Did you mean the Introduction or Related Work?"


def document_of(db: Session, paper: Paper) -> Document:
    return Document.model_validate(repositories.get_current_revision(db, paper).document)


def test_a_chosen_target_overrides_the_one_the_model_read_from_the_prose(
    db: Session, stored_paper: Paper
) -> None:
    """The researcher pointed at a section; the model only read about one.

    Pointing is the more reliable signal, so it wins. Without this, answering
    "which section did you mean?" would still edit whichever section the model
    happened to name.
    """
    document = document_of(db, stored_paper)
    chosen, other = document.sections[0].id, document.sections[1].id
    llm = ScriptedLLM({"intent": routing_llm(EditIntent.SHORTEN_SECTION, other)})

    routed = route(
        "shorten it by 20%",
        document,
        llm,
        Deadline.after("test", 30.0),
        target_section_id=chosen,
    )

    assert routed.section_id == chosen
    assert routed.intent is EditIntent.SHORTEN_SECTION


def test_a_chosen_paragraph_narrows_the_edit_to_it(db: Session, stored_paper: Paper) -> None:
    document = document_of(db, stored_paper)
    section = max(document.sections, key=lambda item: item.word_count())
    paragraph = section.paragraphs[0]
    llm = ScriptedLLM({"intent": routing_llm(EditIntent.SHORTEN_SECTION, section.id)})

    routed = route(
        "shorten this",
        document,
        llm,
        Deadline.after("test", 30.0),
        target_paragraph_id=paragraph.id,
    )

    assert routed.paragraph_id == paragraph.id
    assert routed.section_id == section.id


def test_a_chosen_target_that_no_longer_exists_is_refused(db: Session, stored_paper: Paper) -> None:
    """The UI builds targets from a manuscript that can be a revision behind."""
    document = document_of(db, stored_paper)
    llm = ScriptedLLM({"intent": routing_llm(EditIntent.SHORTEN_SECTION, document.sections[0].id)})

    with pytest.raises(GroundingValidationError):
        route(
            "shorten it by 20%",
            document,
            llm,
            Deadline.after("test", 30.0),
            target_section_id="sec_does_not_exist",
        )


def test_the_question_names_sections_the_way_the_paper_does(
    db: Session, stored_paper: Paper
) -> None:
    """The researcher is asked about their own paper, in their own words.

    The model writes the clarification from an outline carrying ids, so it comes
    back saying "sec_1 (Introduction)". An internal identifier is not something
    to put in front of someone being asked a question about their manuscript.
    """
    document = document_of(db, stored_paper)
    section = document.sections[0]
    llm = ScriptedLLM(
        {
            "intent": {
                "intent": EditIntent.AMBIGUOUS_INTENT.value,
                "clarification": (
                    f"Please specify whether you want to shorten {section.id} "
                    f"({section.title}) or another section."
                ),
            }
        }
    )

    proposal = create_proposal(db, stored_paper.id, "shorten it", operation_id="op_n", llm=llm)

    assert proposal.failure_detail is not None
    assert section.id not in proposal.failure_detail
    assert section.title in proposal.failure_detail
