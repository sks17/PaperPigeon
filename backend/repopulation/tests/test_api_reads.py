"""P4: GET /api/lab and /api/node/description — the read surface for grounded descriptions.

The graph endpoint renders labs as 4 fields and a researcher's `about` only inside a run snapshot, so
the enriched/grounded data is read here. Boots pgserver, loads the legacy graph, adds a small run
(a scraped+described lab with faculty + a described researcher), and asserts the endpoints return the
description, cited evidence, and resolved faculty — and 404 on a missing id or a kind mismatch.
"""
from __future__ import annotations

import json
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
from backend.repopulation.loader import apply_description_updates, load_import_rows  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "public" / "graph_cache.json"

RESEARCHER = "https://openalex.org/A1"
LAB = "lab:https://openalex.org/I100:vision-lab"
EVIDENCE = [{"id": 1, "kind": "topics", "text": "Research topics: Computer Vision."}]


def _node(nid, kind, name, val, attrs, *, ai=None, dmodel=None):
    return {"id": nid, "kind": kind, "name": name, "val": val, "orcid": None,
            "openalex_id": None, "ror": None, "normalized_name": name.lower(),
            "attributes": attrs, "ai_description": ai, "description_model": dmodel,
            "confidence": 1.0, "source_record_key": "oa"}


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        factory = make_session_factory(make_engine(srv.get_uri()))
        with factory() as session:
            load_import_rows(session, cache_to_rows(json.loads(CACHE.read_text(encoding="utf-8"))))
            # A small run: a researcher + a scraped lab whose faculty is that researcher.
            load_import_rows(session, {
                "runs": [{"key": "r", "seed": {"institution": "Test U", "topic": "vision"},
                          "status": "succeeded"}],
                "source_records": [{"key": "oa", "source": "openalex", "source_url": None,
                                    "confidence": None, "evidence": "oa", "run_key": "r",
                                    "raw_s3_key": None}],
                "nodes": [
                    _node(RESEARCHER, "researcher", "Alice Example", 1,
                          {"papers": [], "tags": ["Computer Vision"], "works_count": 3}),
                    _node(LAB, "lab", "Vision Lab", 2,
                          {"description": "We study computer vision.", "pi": "Dr. Vee",
                           "research_areas": ["computer vision"], "faculty": [RESEARCHER],
                           "url": "http://example.test/vision"},
                          ai="We study computer vision.", dmodel="scrape"),
                ],
                "edges": [],
                "relevance": [],
            })
            # Promote a grounded description onto the researcher (as describe_run would).
            apply_description_updates(session, [{
                "node_id": RESEARCHER, "ai_description": "Alice studies computer vision [1].",
                "description_model": "stub-llm",
                "description_generated_at": "2026-06-24T00:00:00+00:00",
                "description_evidence": EVIDENCE,
            }])

        app = api_module.create_app()

        def _override_session():
            with factory() as session:
                yield session

        app.dependency_overrides[api_module.get_session] = _override_session
        yield TestClient(app)
    finally:
        srv.cleanup()


def test_node_description(client: TestClient) -> None:
    resp = client.get("/api/node/description", params={"id": RESEARCHER})
    assert resp.status_code == 200
    body = resp.json()
    assert body["about"] == "Alice studies computer vision [1]."
    assert body["description_model"] == "stub-llm"
    assert body["evidence"] == EVIDENCE
    assert body["kind"] == "researcher"


def test_lab_detail_resolves_faculty(client: TestClient) -> None:
    resp = client.get("/api/lab", params={"id": LAB})
    assert resp.status_code == 200
    body = resp.json()
    assert body["description"] == "We study computer vision."
    assert body["research_areas"] == ["computer vision"]
    assert body["pi"] == "Dr. Vee"
    assert body["faculty"] == [{"id": RESEARCHER, "name": "Alice Example"}]


def test_404_on_missing(client: TestClient) -> None:
    assert client.get("/api/node/description", params={"id": "nope"}).status_code == 404
    assert client.get("/api/lab", params={"id": "nope"}).status_code == 404


def test_lab_endpoint_rejects_non_lab(client: TestClient) -> None:
    # A researcher id is not a lab -> 404 (kind guard).
    assert client.get("/api/lab", params={"id": RESEARCHER}).status_code == 404


def test_list_runs(client: TestClient) -> None:
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    runs = resp.json()
    # The fixture loaded the legacy run (auto-published) + one small unpublished run.
    by_published = {r["published"]: r for r in runs}
    assert True in by_published and False in by_published
    legacy = by_published[True]
    assert legacy["status"] == "succeeded" and legacy["nodes"] == 323  # the legacy run's membership

    other = by_published[False]
    assert other["seed"].get("topic") == "vision"
    assert other["nodes"] == 2 and other["status"] == "succeeded"  # the researcher + lab added
