from __future__ import annotations

from collections import Counter
from collections.abc import Mapping

from app.api.presenters.shared import _author_names, _https_only
from app.api.schemas import (
    InlineCitation,
    InlineRun,
    ManuscriptOut,
    ParagraphOut,
    ReferenceOut,
    SectionOut,
)
from app.db.models import DocumentRevision, Paper, ReferenceResolution
from app.domain.citation import SemanticParseStatus
from app.domain.document import Document, Paragraph, Section, TextRun
from app.domain.reference import ReferenceRecord, ResolutionConfidence, ResolutionMethod


def manuscript(
    paper: Paper,
    revision: DocumentRevision,
    resolutions: Mapping[str, ReferenceResolution] | None = None,
) -> ManuscriptOut:
    document = Document.model_validate(revision.document)
    occurrences = Counter(
        reference_id
        for paragraph in document.paragraphs()
        for citation_id in paragraph.citation_ids
        if (node := document.citations.get(citation_id))
        for reference_id in node.reference_ids
    )

    return ManuscriptOut(
        paper_id=paper.id,
        revision_id=revision.id,
        revision_number=revision.revision_number,
        title=document.title,
        abstract=document.abstract,
        sections=[_section(section, document) for section in document.sections],
        references=[
            _reference(reference, occurrences, (resolutions or {}).get(reference.id))
            for reference in document.references
        ],
        unlinked_citation_ids=sorted(
            node.id for node in document.citations.values() if node.is_unlinked
        ),
        raw_only_citation_ids=sorted(
            document.citations_by_parse_status(SemanticParseStatus.RAW_ONLY)
        ),
    )


def _section(section: Section, document: Document) -> SectionOut:
    return SectionOut(
        id=section.id,
        title=section.title,
        level=section.level,
        order_index=section.order_index,
        paragraphs=[_paragraph(paragraph, document) for paragraph in section.paragraphs],
    )


def _paragraph(paragraph: Paragraph, document: Document) -> ParagraphOut:
    inlines: list[InlineRun] = []
    for node in paragraph.inlines:
        if isinstance(node, TextRun):
            inlines.append(InlineRun(kind="text", text=node.text))
            continue

        occurrence = document.citations.get(node.citation_id)
        if occurrence is None:
            continue
        inlines.append(
            InlineRun(
                kind="citation",
                citation=InlineCitation(
                    citation_id=occurrence.id,
                    raw_marker=occurrence.raw_marker,
                    reference_ids=list(occurrence.reference_ids),
                    parse_status=occurrence.semantic_parse_status,
                    is_unlinked=occurrence.is_unlinked,
                    fidelity_exportable=occurrence.fidelity_exportable,
                ),
            )
        )
    return ParagraphOut(
        id=paragraph.id,
        order_index=paragraph.order_index,
        inlines=inlines,
        word_count=paragraph.word_count(),
    )


def _reference(
    reference: ReferenceRecord,
    occurrences: Counter[str],
    resolution: ReferenceResolution | None = None,
) -> ReferenceOut:
    return ReferenceOut(
        id=reference.id,
        bibliography_order=reference.bibliography_order,
        title=reference.csl.title,
        authors=_author_names(reference.csl),
        year=reference.csl.year,
        container_title=reference.csl.container_title,
        doi=reference.csl.DOI,
        url=_https_only(reference.csl.URL),
        raw_text=reference.raw_text,
        normalization_status=reference.normalization_status,
        resolution_method=(
            ResolutionMethod(resolution.method) if resolution else reference.resolution_method
        ),
        resolution_confidence=(
            ResolutionConfidence(resolution.confidence)
            if resolution
            else reference.resolution_confidence
        ),
        source_record_id=(
            resolution.source_record_id if resolution else reference.source_record_id
        ),
        occurrences=occurrences.get(reference.id, 0),
    )
