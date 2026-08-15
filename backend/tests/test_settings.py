from __future__ import annotations

import pytest

from app.settings import Settings

LLM_VARIABLES = (
    "LLM_PROVIDER",
    "LLM_MODE",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_BASE_URL",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "GROQ_API_KEY",
    "LLM_FALLBACK_PROVIDER",
    "LLM_FALLBACK_API_KEY",
    "LLM_FALLBACK_MODEL",
    "LLM_FALLBACK_BASE_URL",
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in LLM_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def test_gemini_native_key_is_used_by_default() -> None:
    settings = Settings(_env_file=None, gemini_api_key="gemini-key")

    assert settings.llm_provider == "gemini"
    assert settings.llm_model == "gemini-3.5-flash-lite"
    assert settings.llm_base_url == "https://generativelanguage.googleapis.com/v1beta/openai"
    assert settings.effective_llm_api_key == "gemini-key"
    assert settings.llm_configured


def test_generic_key_takes_precedence() -> None:
    settings = Settings(
        _env_file=None,
        llm_api_key="deployment-key",
        gemini_api_key="gemini-key",
    )

    assert settings.effective_llm_api_key == "deployment-key"


def test_openai_native_key_requires_openai_provider() -> None:
    settings = Settings(
        _env_file=None,
        llm_provider="openai",
        openai_api_key="openai-key",
    )

    assert settings.effective_llm_api_key == "openai-key"


def test_groq_native_key_resolves_for_the_groq_provider() -> None:
    settings = Settings(_env_file=None, llm_provider="groq", groq_api_key="groq-key")

    assert settings.effective_llm_api_key == "groq-key"


def test_an_unconfigured_provider_reports_itself_as_unconfigured() -> None:
    settings = Settings(_env_file=None)

    assert settings.effective_llm_api_key == ""
    assert not settings.llm_configured
