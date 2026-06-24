"""P1-T07: importer order-preservation + data-level idempotency.

Asserts that `cache_to_rows` (backend/repopulation/importer/cache_to_rows.py) is:
  * stable — identical input yields identical output across calls,
  * uniquely keyed — node 'id' values and (src_id, dst_id, type) edge tuples don't collide,
  * order-preserving — node_rows / edge_rows follow the input cache order.

Run by the main thread (`python -m pytest -q` from the project root). This file performs no
DB / network I/O; it only loads the read-only legacy cache and exercises the pure importer.
"""
from __future__ import annotations

import copy
import json
from collections import Counter
from pathlib import Path

import pytest

from backend.repopulation.importer.cache_to_rows import (
    RENDER_TO_RICH_EDGE_TYPE,
    cache_to_rows,
)

# test file -> tests/ -> repopulation/ -> backend/ -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE_PATH = PROJECT_ROOT / "public" / "graph_cache.json"


def _load_real_cache() -> dict:
    if not CACHE_PATH.exists():
        pytest.skip(f"legacy cache not found at {CACHE_PATH}")
    with CACHE_PATH.open(encoding="utf-8") as fh:
        return json.load(fh)


# Small, self-contained cache covering both node kinds and all three legacy link types.
INLINE_CACHE: dict = {
    "nodes": [
        {
            "id": "r1",
            "type": "researcher",
            "name": "Ada Researcher",
            "val": 1,
            "advisor": "r2",
            "contact_info": ["ada@example.edu"],
            "labs": ["lab1"],
            "standing": "PhD",
            "papers": [
                {"title": "Paper A", "year": 2020, "document_id": "doc-a", "tags": ["ml"]},
            ],
            "tags": ["ml"],
            "influence": 0.42,
            "about": "Works on machine learning.",
        },
        {
            "id": "r2",
            "type": "researcher",
            "name": "Grace Advisor",
            "val": 1,
            "advisor": None,
            "contact_info": [],
            "labs": ["lab1"],
            "standing": "Professor",
            "papers": [],
            "tags": [],
            "influence": None,
            "about": "",
        },
        {"id": "lab1", "type": "lab", "name": "Vision Lab", "val": 2},
    ],
    "links": [
        {"source": "r1", "target": "r2", "type": "paper"},
        {"source": "r1", "target": "r2", "type": "advisor"},
        {"source": "r1", "target": "lab1", "type": "researcher_lab"},
        {"source": "r2", "target": "lab1", "type": "researcher_lab"},
    ],
}


@pytest.fixture(params=["inline", "real"])
def cache(request: pytest.FixtureRequest) -> dict:
    if request.param == "inline":
        return copy.deepcopy(INLINE_CACHE)
    return _load_real_cache()


def test_output_is_stable(cache: dict) -> None:
    """Identical input -> identical output (data-level idempotency)."""
    first = cache_to_rows(cache)
    second = cache_to_rows(cache)
    assert first == second


def test_does_not_mutate_input(cache: dict) -> None:
    """The importer is pure: it must not mutate the caller's graph dict."""
    snapshot = copy.deepcopy(cache)
    cache_to_rows(cache)
    assert cache == snapshot


def test_node_ids_unique(cache: dict) -> None:
    node_ids = [row["id"] for row in cache_to_rows(cache)["nodes"]]
    assert len(node_ids) == len(set(node_ids))


def test_edge_rows_faithfully_map_input_links(cache: dict) -> None:
    """Edge identities are NOT unique by design: legacy 'paper' links are PARALLEL edges, one
    per co-authored paper (max multiplicity 9 in the real cache = #joint works). The importer
    preserves them 1:1 so the frontend round-trip is exact; the DB-load path later collapses
    each parallel set into a single weighted COAUTHORED_WITH edge (weight = count), which the
    DB-backed serializer expands again. So the invariant here is a faithful MULTISET mapping of
    input links — no edges invented or dropped — and any multiplicity>1 is coauthorship only."""
    edges = cache_to_rows(cache)["edges"]
    actual = Counter((e["src_id"], e["dst_id"], e["type"]) for e in edges)
    expected = Counter(
        (link["source"], link["target"], RENDER_TO_RICH_EDGE_TYPE[link["type"]])
        for link in cache["links"]
    )
    assert actual == expected
    duplicated_types = {identity[2] for identity, count in actual.items() if count > 1}
    assert duplicated_types <= {"COAUTHORED_WITH"}


def test_node_order_matches_input(cache: dict) -> None:
    expected = [node["id"] for node in cache["nodes"]]
    actual = [row["id"] for row in cache_to_rows(cache)["nodes"]]
    assert actual == expected


def test_edge_order_matches_input(cache: dict) -> None:
    rows = cache_to_rows(cache)
    expected = [
        (link["source"], link["target"], RENDER_TO_RICH_EDGE_TYPE[link["type"]])
        for link in cache["links"]
    ]
    actual = [(e["src_id"], e["dst_id"], e["type"]) for e in rows["edges"]]
    assert actual == expected


def test_row_counts_track_input(cache: dict) -> None:
    """One row per input node / link — no drops, no duplicates."""
    rows = cache_to_rows(cache)
    assert len(rows["nodes"]) == len(cache["nodes"])
    assert len(rows["edges"]) == len(cache["links"])
