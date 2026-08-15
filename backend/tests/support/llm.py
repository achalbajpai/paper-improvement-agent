from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.domain.errors import LLMInvalidOutputError, LLMUnavailableError
from app.providers.llm import LLMResult, Prompt

T = TypeVar("T", bound=BaseModel)


class ScriptedLLM:
    def __init__(self, responses: dict[str, Any], model: str = "scripted") -> None:
        self._responses = responses
        self._model = model
        self.calls: list[str] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def configured(self) -> bool:
        return True

    def complete_structured(
        self,
        prompt: Prompt,
        schema: type[T],
        *,
        remaining_seconds: float | None = None,
        temperature: float = 0.0,
    ) -> LLMResult[T]:
        self.calls.append(prompt.name)
        if prompt.name not in self._responses:
            raise LLMUnavailableError(f"No scripted response for prompt {prompt.name!r}.")
        payload = self._responses[prompt.name]
        if callable(payload):
            payload = payload(prompt)
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        try:
            value = schema.model_validate_json(raw)
        except ValidationError as exc:
            raise LLMInvalidOutputError(
                "A scripted response did not satisfy the real schema.",
                prompt=prompt.name,
                errors=str(exc.errors())[:400],
            ) from exc
        return LLMResult(
            value=value,
            model=self._model,
            provider="scripted",
            prompt_name=prompt.name,
            prompt_version=prompt.version,
            repaired=False,
        )
