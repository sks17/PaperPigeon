from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from backend.repopulation.importer.cache_to_rows import cache_to_rows
from backend.repopulation.serializers.graph_data import serialize_graph


PROJECT_ROOT = Path(__file__).resolve().parents[3]
GRAPH_CACHE_PATH = PROJECT_ROOT / "public" / "graph_cache.json"


def _load_graph_cache() -> dict:
    return json.loads(GRAPH_CACHE_PATH.read_text(encoding="utf-8"))


def _legacy_relevance_by_node(rows: dict) -> dict[str, float]:
    return {
        relevance["node_id"]: relevance["score"]
        for relevance in rows["relevance"]
        if relevance["run_key"] == "legacy"
    }


def test_cache_import_and_graph_serialization_round_trip_preserves_cache() -> None:
    original_graph = _load_graph_cache()

    rows = cache_to_rows(original_graph)
    rendered_graph = serialize_graph(
        rows["nodes"],
        rows["edges"],
        _legacy_relevance_by_node(rows),
    )

    assert rendered_graph == original_graph


def test_graph_cache_has_expected_legacy_contract_counts() -> None:
    graph = _load_graph_cache()

    node_counts = Counter(node["type"] for node in graph["nodes"])
    link_counts = Counter(link["type"] for link in graph["links"])

    assert node_counts == {"researcher": 298, "lab": 25}
    assert link_counts == {"paper": 633, "advisor": 190, "researcher_lab": 220}
