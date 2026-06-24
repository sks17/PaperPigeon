"""Embeddings client — OpenRouter, openai/text-embedding-3-small (1536d). Main-thread code.

1536d matches the repop.embedding pgvector column (no migration). Batched + raw-cached (re-embedding
identical text is a cache hit). When a `budget` (DailyBudget) is supplied, each batch's estimated
cost is charged BEFORE the call, so an over-budget run stops cleanly (PAPERPIGEON_BUDGET_PRO_DAILY_USD).
"""
from __future__ import annotations

from backend.repopulation.clients.budget import estimate_embed_cost
from backend.repopulation.clients.http import HttpClient

OPENROUTER_HOST = "openrouter.ai"


class EmbeddingsClient:
    URL = "https://openrouter.ai/api/v1/embeddings"
    DEFAULT_MODEL = "openai/text-embedding-3-small"

    def __init__(self, http: HttpClient, api_key: str, *, model: str = DEFAULT_MODEL,
                 batch_size: int = 64, budget=None) -> None:
        self._http = http
        self._api_key = api_key
        self.model = model
        self._batch_size = batch_size
        self._budget = budget

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            if self._budget is not None:
                self._budget.charge(estimate_embed_cost(batch), "embeddings batch")
            body, _ = self._http.post_json(
                self.URL,
                {"model": self.model, "input": batch},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            for item in sorted(body["data"], key=lambda d: d["index"]):
                out.append(item["embedding"])
        return out
