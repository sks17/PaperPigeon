"""Embeddings client — OpenRouter, openai/text-embedding-3-small (1536d). Main-thread code.

1536d matches the repop.embedding pgvector column (no migration). Batched + raw-cached (re-embedding
identical text is a cache hit). Spend is currently bounded only indirectly — the number of texts is
capped by the discovery page caps (<= max_author_pages * 200 researchers). A hard OPENROUTER_BUDGET
dollar ceiling is NOT yet enforced here (TODO: wire a pre-call cost cap); do not rely on it.
"""
from __future__ import annotations

from backend.repopulation.clients.http import HttpClient

OPENROUTER_HOST = "openrouter.ai"


class EmbeddingsClient:
    URL = "https://openrouter.ai/api/v1/embeddings"
    DEFAULT_MODEL = "openai/text-embedding-3-small"

    def __init__(self, http: HttpClient, api_key: str, *, model: str = DEFAULT_MODEL,
                 batch_size: int = 64) -> None:
        self._http = http
        self._api_key = api_key
        self.model = model
        self._batch_size = batch_size

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            body, _ = self._http.post_json(
                self.URL,
                {"model": self.model, "input": batch},
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            for item in sorted(body["data"], key=lambda d: d["index"]):
                out.append(item["embedding"])
        return out
