from __future__ import annotations

from dataclasses import dataclass

from app.domain.document import Document
from app.domain.errors import AmbiguousIntentError, UnsupportedIntentError
from app.providers.llm import StructuredLLM
from app.services.deadline import Deadline
from app.services.editor import intent as intent_prompt
from app.services.editor.intent import EditIntent
from app.services.research.allowlist import Allowlist, require_choice


@dataclass(frozen=True)
class RoutedCommand:
    intent: EditIntent
    section_id: str | None = None
    paragraph_id: str | None = None
    target_reduction_ratio: float | None = None


def route(
    command: str,
    document: Document,
    llm: StructuredLLM,
    deadline: Deadline,
    *,
    target_section_id: str | None = None,
    target_paragraph_id: str | None = None,
) -> RoutedCommand:
    outline = [(section.id, section.title) for section in document.sections]
    sections = Allowlist("section", {section.id: section for section in document.sections})
    paragraphs = Allowlist(
        "paragraph", {paragraph.id: paragraph for paragraph in document.paragraphs()}
    )

    chosen_section = _validated(sections, target_section_id, prompt="target")
    chosen_paragraph = _validated(paragraphs, target_paragraph_id, prompt="target")
    if chosen_paragraph and not chosen_section:
        chosen_section = _section_of(document, chosen_paragraph)

    prompt = intent_prompt.build_prompt(
        command, outline, _describe(document, chosen_section, chosen_paragraph)
    )
    response = llm.complete_structured(
        prompt, intent_prompt.IntentResponse, remaining_seconds=deadline.remaining()
    ).value

    chosen = EditIntent(
        require_choice(
            response.intent,
            [member.value for member in EditIntent],
            prompt=prompt.name,
            field="intent",
        )
    )

    if chosen is EditIntent.UNSUPPORTED_INTENT:
        raise UnsupportedIntentError(
            "This system can shorten a section or add supporting citations, and "
            "this command asks for something else.",
            supported=[EditIntent.SHORTEN_SECTION.value, EditIntent.ADD_SUPPORTING_CITATIONS.value],
        )
    if chosen is EditIntent.AMBIGUOUS_INTENT:
        raise AmbiguousIntentError(
            "This command does not say which part of the paper it applies to.",
            clarification=_named(document, response.clarification or "")[:300],
        )

    if response.section_id is not None:
        sections.resolve(response.section_id, prompt=prompt.name)
    if response.paragraph_id is not None:
        paragraphs.resolve(response.paragraph_id, prompt=prompt.name)

    paragraph_id = chosen_paragraph or response.paragraph_id
    section_id = (
        chosen_section or response.section_id or _section_of(document, response.paragraph_id)
    )
    if section_id is None:
        raise AmbiguousIntentError(
            "This command does not identify a section or paragraph to act on.",
            clarification="Name the section to edit.",
        )

    return RoutedCommand(
        intent=chosen,
        section_id=section_id,
        paragraph_id=paragraph_id,
        target_reduction_ratio=response.target_reduction_ratio,
    )


def _validated[T](allowlist: Allowlist[T], value: str | None, *, prompt: str) -> str | None:
    if value is None:
        return None
    allowlist.resolve(value, prompt=prompt)
    return value


def _named(document: Document, text: str) -> str:
    for section in document.sections:
        if not section.title:
            continue
        text = text.replace(f"{section.id} ({section.title})", section.title)
        text = text.replace(section.id, section.title)
    return text


def _describe(document: Document, section_id: str | None, paragraph_id: str | None) -> str | None:
    if section_id is None:
        return None
    section = document.section(section_id)
    title = section.title if section and section.title else section_id
    if paragraph_id is None:
        return f"Section {section_id}: {title}"
    return f"One paragraph ({paragraph_id}) of section {section_id}: {title}"


def _section_of(document: Document, paragraph_id: str | None) -> str | None:
    if paragraph_id is None:
        return None
    section = document.section_of(paragraph_id)
    return section.id if section else None
