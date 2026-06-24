"""Query-scoped relevance scoring  [Cursor task P2-T02].

Implement `score_relevance` per DISCOVERY.md:
  relevance = w1*cosine(seed_embedding, node_embedding)
            + w2*recency_decay(last_active_year)
            + w3*normalized_log1p(output_or_citation_volume)

PURE: no network, no DB, NO wall-clock (Date/now is forbidden in pure code) — the caller passes
`current_year`. Returns relevance_row dicts (SCHEMA.md §2) scoped to `run_key`, with `components`
populated for explainability. The main thread embeds texts, supplies vectors, and persists rows.

Forbidden: importing clients/*, requests/httpx/urllib, DB access.
"""
from __future__ import annotations

import math

DEFAULT_WEIGHTS = {"cosine": 0.6, "recency": 0.2, "volume": 0.2}
DEFAULT_HALFLIFE_YEARS = 5.0


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity in [-1.0, 1.0]; 0.0 if either vector is empty or zero-magnitude.

    Pairs are taken element-wise; if the vectors differ in length the shorter length wins
    (defensive — embeddings of one model are same-dim, so this only guards bad input)."""
    if not a or not b:
        return 0.0

    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def recency_decay(last_active_year: int | None, current_year: int,
                  halflife: float = DEFAULT_HALFLIFE_YEARS) -> float:
    """exp(-(current_year - last_active_year)/halflife), clamped to [0.0, 1.0].

    Returns 0.0 when the year is unknown (None). A node active in the current year (or a
    future-dated year, defensively) scores 1.0; older years decay toward 0.0. Pure — the
    caller supplies `current_year`; the wall clock is never read here."""
    if last_active_year is None:
        return 0.0
    if halflife <= 0.0:
        return 0.0

    age = current_year - last_active_year
    if age <= 0:
        return 1.0

    return min(1.0, math.exp(-age / halflife))


def score_relevance(
    seed_embedding: list[float],
    node_vectors: dict[str, list[float]],
    node_meta: dict[str, dict],
    run_key: str,
    current_year: int,
    weights: dict = DEFAULT_WEIGHTS,
) -> list[dict]:
    """Return relevance_row dicts (node_id, run_key, score, components) for each node in node_vectors.

    relevance = w1*cosine(seed_embedding, node_embedding)
              + w2*recency_decay(last_active_year, current_year)
              + w3*normalized_log1p(output_or_citation_volume)

    node_meta[node_id] = {"last_year": int|None, "volume": number} (missing entries → defaults).
    `components` is populated for explainability (SQL `repop.relevance.components` jsonb).
    Pure: no network, no DB, no wall-clock — `current_year` is supplied by the caller.
    """
    w_cosine = weights.get("cosine", 0.0)
    w_recency = weights.get("recency", 0.0)
    w_volume = weights.get("volume", 0.0)

    raw_volume_by_node = {
        node_id: math.log1p((node_meta.get(node_id) or {}).get("volume") or 0)
        for node_id in node_vectors
    }
    max_raw_volume = max(raw_volume_by_node.values(), default=0.0)

    rows: list[dict] = []
    for node_id, vector in node_vectors.items():
        meta = node_meta.get(node_id) or {}

        cos = cosine(seed_embedding, vector)
        rec = recency_decay(meta.get("last_year"), current_year)
        raw_vol = raw_volume_by_node[node_id]
        vol = raw_vol / max_raw_volume if max_raw_volume > 0.0 else 0.0

        score = w_cosine * cos + w_recency * rec + w_volume * vol

        rows.append({
            "node_id": node_id,
            "run_key": run_key,
            "score": score,
            "components": {
                "cosine": cos,
                "recency": rec,
                "volume": vol,
                "volume_raw": raw_vol,
                "weights": dict(weights),
            },
        })

    return rows
