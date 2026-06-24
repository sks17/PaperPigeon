"""Integration test: unpublished repopulation runs do not change the default graph snapshot."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("pgserver")

import pgserver  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import graph_from_db, load_import_rows, publish_run  # noqa: E402
from backend.repopulation.models.nodes import RepopulationRun  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "public" / "graph_cache.json"

SECOND_RUN_SEED = {
    "institution": "Snapshot Isolation University",
    "topic": "graph data",
    "keywords": ["snapshot", "isolation"],
    "openalex_institution_id": "https://openalex.org/I-snapshot-isolation",
}


def _second_import_rows() -> dict:
    return {
        "runs": [
            {
                "key": "snapshot-isolation-run",
                "seed": SECOND_RUN_SEED,
                "status": "succeeded",
            }
        ],
        "source_records": [
            {
                "key": "snapshot-isolation-source",
                "source": "openalex",
                "source_url": None,
                "retrieved_at": None,
                "confidence": 1.0,
                "evidence": "inline snapshot isolation fixture",
                "run_key": "snapshot-isolation-run",
                "raw_s3_key": None,
            }
        ],
        "nodes": [
            {
                "id": "snapshot-researcher",
                "kind": "researcher",
                "name": "Snapshot Researcher",
                "val": 1,
                "orcid": None,
                "openalex_id": "https://openalex.org/A-snapshot-researcher",
                "ror": None,
                "normalized_name": "snapshot researcher",
                "attributes": {
                    "advisor": None,
                    "contact_info": [],
                    "labs": [],
                    "standing": None,
                    "papers": [],
                    "tags": [],
                },
                "ai_description": None,
                "description_model": None,
                "description_generated_at": None,
                "description_evidence": None,
                "confidence": 1.0,
                "source_record_key": "snapshot-isolation-source",
            },
            {
                "id": "snapshot-lab",
                "kind": "lab",
                "name": "Snapshot Lab",
                "val": 2,
                "orcid": None,
                "openalex_id": None,
                "ror": None,
                "normalized_name": "snapshot lab",
                "attributes": {},
                "ai_description": None,
                "description_model": None,
                "description_generated_at": None,
                "description_evidence": None,
                "confidence": 1.0,
                "source_record_key": "snapshot-isolation-source",
            },
        ],
        "edges": [
            {
                "src_id": "snapshot-researcher",
                "dst_id": "snapshot-lab",
                "type": "MEMBER_OF",
                "weight": 1.0,
                "directed": True,
                "attributes": {},
                "confidence": 1.0,
                "source_record_key": "snapshot-isolation-source",
            }
        ],
        "relevance": [],
    }


def _graph_counts(graph: dict) -> tuple[int, int]:
    return len(graph["nodes"]), len(graph["links"])


def _nodes_by_id(graph: dict) -> dict:
    return {node["id"]: node for node in graph["nodes"]}


def _link_multiset(graph: dict) -> Counter:
    return Counter((link["source"], link["target"], link["type"]) for link in graph["links"])


def _assert_second_graph_only(graph: dict) -> None:
    assert _nodes_by_id(graph) == {
        "snapshot-researcher": {
            "id": "snapshot-researcher",
            "name": "Snapshot Researcher",
            "type": "researcher",
            "val": 1,
            "advisor": None,
            "contact_info": [],
            "labs": [],
            "standing": None,
            "papers": [],
            "tags": [],
            "influence": None,
            "about": None,
        },
        "snapshot-lab": {
            "id": "snapshot-lab",
            "name": "Snapshot Lab",
            "type": "lab",
            "val": 2,
        },
    }
    assert _link_multiset(graph) == Counter(
        {("snapshot-researcher", "snapshot-lab", "researcher_lab"): 1}
    )


@pytest.fixture(scope="module")
def session_factory(tmp_path_factory: pytest.TempPathFactory):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))

        factory = make_session_factory(make_engine(srv.get_uri()))
        with factory() as session:
            legacy_graph = json.loads(CACHE.read_text(encoding="utf-8"))
            load_import_rows(session, cache_to_rows(legacy_graph))
            load_import_rows(session, _second_import_rows())

        yield factory
    finally:
        srv.cleanup()


def test_unpublished_run_is_isolated_from_default_snapshot(session_factory) -> None:
    with session_factory() as session:
        second_run_id = session.scalar(
            select(RepopulationRun.id).where(RepopulationRun.seed == SECOND_RUN_SEED)
        )
        assert second_run_id is not None

        assert _graph_counts(graph_from_db(session)) == (323, 1043)

        second_graph = graph_from_db(session, run_id=second_run_id)
        _assert_second_graph_only(second_graph)

        publish_run(session, second_run_id)
        _assert_second_graph_only(graph_from_db(session))
