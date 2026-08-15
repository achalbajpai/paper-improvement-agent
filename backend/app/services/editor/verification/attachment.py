from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models import SourceRecord
from app.domain.review import SupportVerdict
from app.domain.verification import (
    BlockerCode,
    CheckName,
    CheckStatus,
    VerificationBlocker,
    VerificationCheck,
    VerificationWarning,
    WarningCode,
)
from app.providers.llm import StructuredLLM
from app.services.citations.context_extractor import span_listing, split_abstract
from app.services.deadline import Deadline
from app.services.editor.context import CitationContext, changed_contexts, contexts_for
from app.services.editor.verification.inputs import (
    INTACT,
    UNVERIFIABLE,
    VerificationInputs,
    _Accumulator,
)
from app.services.research.allowlist import Allowlist, require_choice
from app.services.review import support as support_prompt


def _check_attachment(
    inputs: VerificationInputs,
    state: _Accumulator,
    content_hash: str,
    *,
    session: Session | None,
    llm: StructuredLLM | None,
    deadline: Deadline | None,
) -> None:
    before = contexts_for(inputs.base)
    after = contexts_for(inputs.candidate)
    affected = changed_contexts(before, after)

    if not affected:
        state.checks.append(
            VerificationCheck(
                name=CheckName.SEMANTIC_ATTACHMENT,
                status=CheckStatus.PASSED,
                detail="No surviving citation had its claim reworded.",
            )
        )
        return

    if session is None or llm is None or deadline is None:
        state.warnings.append(_unverifiable_warning(inputs, list(affected), content_hash))
        state.checks.append(
            VerificationCheck(
                name=CheckName.SEMANTIC_ATTACHMENT,
                status=CheckStatus.NOT_RUN,
                detail=(
                    f"{len(affected)} citations changed context and could not be "
                    "re-verified in this configuration."
                ),
            )
        )
        return

    verified = 0
    unverifiable: list[str] = []
    contradicted: list[str] = []

    for citation_id in affected:
        if deadline.expired:
            unverifiable.append(citation_id)
            continue

        verdicts = _reverify(
            inputs,
            citation_id,
            after[citation_id],
            session=session,
            llm=llm,
            deadline=deadline,
        )
        verdict = _worst(verdicts)
        if verdict is SupportVerdict.CONTRADICTED:
            contradicted.append(citation_id)
        elif verdict in UNVERIFIABLE:
            unverifiable.append(citation_id)
        elif verdict in INTACT:
            verified += 1
        else:
            state.warnings.append(
                VerificationWarning.build(
                    WarningCode.ATTACHMENT_INVALIDATED,
                    (
                        f"The sentence citing {_marker(inputs, citation_id)} was reworded, "
                        "and its source now supports the new wording only in part."
                    ),
                    (citation_id,),
                    content_hash,
                )
            )

    if contradicted:
        state.blockers.append(
            VerificationBlocker(
                code=BlockerCode.ATTACHMENT_CONTRADICTED,
                message=(
                    "After this edit, a citation's own source contradicts the sentence "
                    "it is attached to."
                ),
                subject_ids=tuple(contradicted),
            )
        )

    if unverifiable:
        state.warnings.append(_unverifiable_warning(inputs, unverifiable, content_hash))

    state.checks.append(
        VerificationCheck(
            name=CheckName.SEMANTIC_ATTACHMENT,
            status=(
                CheckStatus.BLOCKED
                if contradicted
                else CheckStatus.WARNED
                if unverifiable or verified < len(affected)
                else CheckStatus.PASSED
            ),
            detail=(
                f"{len(affected)} citations changed context: {verified} still supported, "
                f"{len(unverifiable)} unverifiable, {len(contradicted)} contradicted."
            ),
        )
    )


def _reverify(
    inputs: VerificationInputs,
    citation_id: str,
    context: CitationContext,
    *,
    session: Session,
    llm: StructuredLLM,
    deadline: Deadline,
) -> list[tuple[str, SupportVerdict]]:
    node = inputs.candidate.citations.get(citation_id)
    reference_ids = [item.reference_id for item in node.items if item.reference_id] if node else []
    if not reference_ids:
        return [("", SupportVerdict.SOURCE_UNRESOLVED)]

    verdicts: list[tuple[str, SupportVerdict]] = []
    for reference_id in reference_ids:
        if deadline.expired:
            verdicts.append((reference_id, SupportVerdict.EVIDENCE_UNAVAILABLE))
            continue
        verdicts.append(
            (
                reference_id,
                _reverify_one(
                    inputs, reference_id, context, session=session, llm=llm, deadline=deadline
                ),
            )
        )
    return verdicts


def _reverify_one(
    inputs: VerificationInputs,
    reference_id: str,
    context: CitationContext,
    *,
    session: Session,
    llm: StructuredLLM,
    deadline: Deadline,
) -> SupportVerdict:
    record = _snapshot_for(session, inputs, reference_id)
    if record is None or not record.abstract:
        return SupportVerdict.EVIDENCE_UNAVAILABLE

    spans = split_abstract(record.abstract)
    if not spans:
        return SupportVerdict.EVIDENCE_UNAVAILABLE

    allowlist = Allowlist("span", {span.id: span for span in spans})
    prompt = support_prompt.build_prompt(context.host_text, span_listing(spans))
    response = llm.complete_structured(
        prompt, support_prompt.SupportResponse, remaining_seconds=deadline.remaining()
    ).value
    allowlist.resolve_all(response.evidence_span_ids, prompt=prompt.name)

    return SupportVerdict(
        require_choice(
            response.verdict,
            [verdict.value for verdict in support_prompt.MODEL_SELECTABLE_VERDICTS],
            prompt=prompt.name,
            field="verdict",
        )
    )


def _worst(verdicts: list[tuple[str, SupportVerdict]]) -> SupportVerdict:
    if not verdicts:
        return SupportVerdict.SOURCE_UNRESOLVED
    return max((verdict for _, verdict in verdicts), key=_SEVERITY.__getitem__)


_SEVERITY: dict[SupportVerdict, int] = {
    SupportVerdict.SUPPORTED: 0,
    SupportVerdict.PARTIALLY_SUPPORTED: 1,
    SupportVerdict.UNVERIFIABLE_FROM_AVAILABLE_EVIDENCE: 2,
    SupportVerdict.EVIDENCE_UNAVAILABLE: 2,
    SupportVerdict.SOURCE_IDENTITY_UNCERTAIN: 2,
    SupportVerdict.SOURCE_UNRESOLVED: 2,
    SupportVerdict.CONTRADICTED: 3,
}


def _snapshot_for(
    session: Session, inputs: VerificationInputs, reference_id: str
) -> SourceRecord | None:
    reference = inputs.candidate.reference(reference_id)
    if reference is None:
        return None

    query = session.query(SourceRecord).filter(SourceRecord.paper_id == inputs.paper_id)

    if reference.source_record_id:
        record = query.filter(SourceRecord.id == reference.source_record_id).first()
        if record is not None:
            return record

    doi = reference.csl.DOI
    external = reference.csl.custom.get("external_id") if reference.csl.custom else None
    if doi:
        record = query.filter(SourceRecord.doi == doi).first()
        if record is not None:
            return record
    if isinstance(external, str) and external:
        return query.filter(SourceRecord.external_id == external).first()
    return None


def _unverifiable_warning(
    inputs: VerificationInputs, citation_ids: Sequence[str], content_hash: str
) -> VerificationWarning:
    markers = [_marker(inputs, citation_id) for citation_id in citation_ids]
    if len(markers) == 1:
        subject = f"The sentence citing {markers[0]} was"
    else:
        subject = f"{len(markers)} sentences, citing {', '.join(markers)}, were"
    return VerificationWarning.build(
        WarningCode.ATTACHMENT_INVALIDATED,
        (
            f"{subject} reworded, and support could not be re-verified because "
            "those sources' evidence is unavailable."
        ),
        tuple(citation_ids),
        content_hash,
    )


def _marker(inputs: VerificationInputs, citation_id: str) -> str:
    node = inputs.base.citations.get(citation_id)
    return (node.raw_marker if node and node.raw_marker else citation_id).strip()
