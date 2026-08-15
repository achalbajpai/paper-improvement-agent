from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: str = "gemini"
    llm_api_key: str = ""
    llm_model: str = "gemini-3.5-flash-lite"
    llm_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    llm_fallback_provider: str = ""
    llm_fallback_api_key: str = ""
    llm_fallback_model: str = ""
    llm_fallback_base_url: str = ""

    gemini_api_key: str = ""
    openai_api_key: str = ""

    groq_api_key: str = ""

    openalex_api_key: str = ""
    openalex_mailto: str = ""
    semantic_scholar_api_key: str = ""

    database_url: str = "postgresql+psycopg://answerthis:answerthis@postgres:5432/answerthis"
    grobid_url: str = "http://grobid:8070"
    data_dir: Path = Path("/data")

    grobid_timeout_seconds: float = 90.0
    provider_timeout_seconds: float = 10.0
    llm_timeout_seconds: float = 90.0
    llm_max_calls_per_operation: int = 1200
    llm_max_output_tokens: int = 2048
    pandoc_timeout_seconds: float = 60.0
    s2_max_retry_after_seconds: float = 5.0
    semantic_scholar_min_interval_seconds: float = 1.05
    openalex_min_interval_seconds: float = 0.11
    llm_max_retry_after_seconds: float = 30.0
    llm_max_rate_limit_waits: int = 4

    parse_deadline_seconds: float = 180.0
    review_deadline_seconds: float = 3600.0
    proposal_deadline_seconds: float = 300.0
    export_deadline_seconds: float = 180.0

    max_review_paragraphs: int = 500
    max_claims_per_paragraph: int = 3
    max_missing_work_claims: int = 12
    max_suggestions_per_claim: int = 3
    max_citations_per_claim: int = 2
    max_add_citation_paragraphs: int = 3
    max_added_citations_per_operation: int = 4
    max_retrieval_queries: int = 4
    max_candidates_per_query: int = 10
    max_provider_calls_per_operation: int = 400
    max_openalex_calls_per_operation: int = 300
    max_resolution_candidates: int = 5
    max_upload_bytes: int = 50 * 1024 * 1024
    trash_retention_seconds: int = 24 * 3600

    shorten_extractive_only: bool = False
    """Whether SHORTEN_SECTION may only delete whole sentences.

    Defaults closed. Generative shortening is safe only if the novelty validator
    catches an unsupported assertion introduced by the rewriter, and that is a
    property of a specific model, not of this code: the model-safety go/no-go decides
    it, per model, against eight adversarial pairs. Until a live run has passed
    for the model actually configured, the honest default is the one that cannot
    invent a claim -- deleting a sentence never asserts anything new.

    Set ``SHORTEN_EXTRACTIVE_ONLY=false`` only after a passing live
    model-safety check for the same ``LLM_MODEL``.
    """

    cache_ttl_success_seconds: int = 7 * 24 * 3600
    cache_ttl_no_results_seconds: int = 6 * 3600

    @property
    def papers_dir(self) -> Path:
        return self.data_dir / "papers"

    @property
    def trash_dir(self) -> Path:
        return self.data_dir / "trash"

    @property
    def llm_configured(self) -> bool:
        return bool(self.effective_llm_api_key)

    @property
    def effective_llm_api_key(self) -> str:
        if self.llm_api_key:
            return self.llm_api_key
        if self.llm_provider.lower() == "gemini":
            return self.gemini_api_key
        if self.llm_provider.lower() == "openai":
            return self.openai_api_key
        if self.llm_provider.lower() == "groq":
            return self.groq_api_key
        return ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
