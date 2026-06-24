"""LLM client for grounded extraction — OpenRouter chat completions (main-thread integration).

PURE DATA TRANSFORM by construction: no tools / no function-calling are ever sent, so the model can
trigger nothing — scraped HTML is untrusted and may carry prompt injection (04-infra-security → Security).
The caller frames page text as DATA in the system prompt and STRUCTURALLY validates the returned JSON
(extraction/lab_schema.validate) before it can affect the graph. JSON-object response mode + schema in
the prompt (widely supported); budget-charged per call; model tiering via `escalate_model`.
"""
from __future__ import annotations

import json

from backend.repopulation.clients.budget import estimate_chat_cost
from backend.repopulation.clients.http import HttpClient

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"


class LlmError(RuntimeError):
    pass


class LlmClient:
    DEFAULT_MODEL = "google/gemini-2.5-flash-lite"   # cheapest valid Gemini Flash on OpenRouter
    DEFAULT_ESCALATE_MODEL = "google/gemini-2.5-flash"

    def __init__(
        self,
        http: HttpClient,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        escalate_model: str | None = None,
        budget=None,
        temperature: float = 0.0,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self.model = model
        self.escalate_model = escalate_model
        self._budget = budget
        self._temperature = temperature

    def complete_json(self, system: str, user: str, *, model: str | None = None) -> dict:
        """Return the model's JSON object. No tools are sent (data-transform only). Raises LlmError
        on a non-JSON response."""
        chosen = model or self.model
        if self._budget is not None:
            self._budget.charge(estimate_chat_cost(len(system) + len(user)), f"llm:{chosen}")
        body = {
            "model": chosen,
            "temperature": self._temperature,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        resp, _ = self._http.post_json(
            OPENROUTER_CHAT_URL, body, headers={"Authorization": f"Bearer {self._api_key}"}
        )
        try:
            content = resp["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlmError(f"unexpected chat response shape: {exc}") from exc
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError) as exc:
            raise LlmError(f"model did not return valid JSON: {exc}") from exc
