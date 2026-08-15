from __future__ import annotations

import inspect
from typing import Any

import pytest

from app.domain.errors import LLMInvalidOutputError, LLMNotConfiguredError, LLMUnavailableError
from app.providers import llm as llm_module
from app.providers.llm import LLMClient, Prompt, build_llm, parse_completion
from app.services.review.support import SupportResponse
from app.settings import Settings, get_settings
from tests.support.llm import ScriptedLLM

JUDGEMENT_PROMPTS = ("support", "rerank", "selection", "novelty")


def test_the_only_configurable_client_is_the_real_one() -> None:
    assert isinstance(build_llm(), LLMClient)


def test_no_setting_can_select_a_different_implementation() -> None:
    fields = set(Settings.model_fields)

    assert "llm_mode" not in fields
    assert not [name for name in fields if "recorded" in name or "offline" in name]


def test_build_llm_does_not_branch_on_configuration() -> None:
    source = inspect.getsource(build_llm)

    assert "if" not in source.replace("if the", ""), source


def test_the_adapter_module_defines_no_fabricating_client() -> None:
    clients = [
        name
        for name, value in vars(llm_module).items()
        if inspect.isclass(value)
        and hasattr(value, "complete_structured")
        and name not in {"LLMClient", "StructuredLLM"}
    ]

    assert clients == [], clients


def test_scripted_answers_only_what_a_test_handed_it() -> None:
    scripted = ScriptedLLM({})
    with pytest.raises(LLMUnavailableError):
        scripted.complete_structured(
            Prompt(name="support", version="t", system="s", user="u"), SupportResponse
        )


@pytest.mark.parametrize("prompt_name", JUDGEMENT_PROMPTS)
def test_a_judgement_prompt_needs_a_configured_provider(prompt_name: str) -> None:
    client = LLMClient(api_key="", model="none")
    with pytest.raises(LLMNotConfiguredError):
        client.complete_structured(
            Prompt(name=prompt_name, version="t", system="s", user="u"), SupportResponse
        )


def test_readiness_reports_no_mode() -> None:
    assert not hasattr(get_settings(), "llm_mode")


@pytest.mark.parametrize(
    "body",
    [None, [], {}, {"choices": []}, {"choices": [None]}, {"choices": [{"message": []}]}],
)
def test_malformed_provider_responses_are_typed(body: Any) -> None:
    with pytest.raises(LLMInvalidOutputError):
        parse_completion(body)


def test_non_string_completion_content_is_not_coerced_into_model_output() -> None:
    with pytest.raises(LLMInvalidOutputError):
        parse_completion({"choices": [{"message": {"content": {"verdict": "SUPPORTED"}}}]})
