from __future__ import annotations

import json
from numbers import Real
from pathlib import Path

from backend.repopulation.importer.cache_to_rows import (
    RENDER_TO_RICH_EDGE_TYPE,
    cache_to_rows,
)


def _load_public_graph_cache() -> dict:
    repo_root = Path(__file__).resolve().parents[3]
    with (repo_root / "public" / "graph_cache.json").open(encoding="utf-8") as cache_file:
        return json.load(cache_file)


def test_importer_provenance_and_edge_integrity() -> None:
    rows = cache_to_rows(_load_public_graph_cache())

    source_record_keys = {source_record["key"] for source_record in rows["source_records"]}
    node_ids = {node_row["id"] for node_row in rows["nodes"]}
    rich_edge_types = set(RENDER_TO_RICH_EDGE_TYPE.values())

    for node_row in rows["nodes"]:
        assert node_row["source_record_key"] in source_record_keys

    for edge_row in rows["edges"]:
        assert edge_row["source_record_key"] in source_record_keys
        assert edge_row["src_id"] in node_ids
        assert edge_row["dst_id"] in node_ids
        assert edge_row["type"] in rich_edge_types
        assert isinstance(edge_row["weight"], Real)
        assert not isinstance(edge_row["weight"], bool)
        assert isinstance(edge_row["directed"], bool)
