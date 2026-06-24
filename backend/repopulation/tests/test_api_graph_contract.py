"""Integration test: the new FastAPI GET /api/graph/data reproduces the existing graph.

Boots a real local Postgres+pgvector (pgserver, no Docker), applies the migration, loads the
legacy cache, and asserts the endpoint returns the same nodes (full content) and the same link
multiset (1043, incl. parallel coauthorship expanded from weighted edges). Skipped automatically
where the integration deps aren't installed (e.g. the lean CI pure-suite run).
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

pytest.importorskip("pgserver")
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import pgserver  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.repopulation import api as api_module  # noqa: E402
from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import load_import_rows  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "public" / "graph_cache.json"


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        factory = make_session_factory(make_engine(srv.get_uri()))
        with factory() as session:
            load_import_rows(session, cache_to_rows(json.loads(CACHE.read_text(encoding="utf-8"))))

        app = api_module.create_app()

        def _override_session():
            with factory() as session:
                yield session

        app.dependency_overrides[api_module.get_session] = _override_session
        yield TestClient(app)
    finally:
        srv.cleanup()


def _nodes_by_id(graph: dict) -> dict:
    return {n["id"]: n for n in graph["nodes"]}


def _link_multiset(graph: dict) -> Counter:
    return Counter((l["source"], l["target"], l["type"]) for l in graph["links"])


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_graph_data_reproduces_existing_graph(client: TestClient) -> None:
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    resp = client.get("/api/graph/data")
    assert resp.status_code == 200
    graph = resp.json()
    assert _nodes_by_id(graph) == _nodes_by_id(cache)
    assert _link_multiset(graph) == _link_multiset(cache)
    assert len(graph["links"]) == 1043
