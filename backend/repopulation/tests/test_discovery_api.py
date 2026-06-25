"""P5: discovery endpoints — auth, validation, enqueue, dedup/cache (TestClient + pgserver).

POST/GET /api/discover are gated by X-Discovery-Key (fail-closed). A valid POST enqueues a queued job;
re-POSTing a live seed returns the same job; a previously succeeded seed returns its run as a cache
hit; bad input is 422. Mirrors tests/test_api_reads.py.
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
from sqlalchemy import select  # noqa: E402

from backend.repopulation import api as api_module  # noqa: E402
from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.discovery_service import build_seed, seed_hash  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import get_published_run_id, load_import_rows  # noqa: E402
from backend.repopulation.models.discovery_job import DiscoveryJob  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "public" / "graph_cache.json"
KEY = "test-discovery-key"
HEADERS = {"X-Discovery-Key": KEY}


@pytest.fixture(scope="module")
def env(tmp_path_factory, request):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for m in migration_files():
            srv.psql(m.read_text(encoding="utf-8"))
        factory = make_session_factory(make_engine(srv.get_uri()))
        with factory() as s:
            load_import_rows(s, cache_to_rows(json.loads(CACHE.read_text("utf-8"))))
        app = api_module.create_app()

        def _override():
            with factory() as s:
                yield s

        app.dependency_overrides[api_module.get_session] = _override
        yield TestClient(app), factory
    finally:
        srv.cleanup()


@pytest.fixture(autouse=True)
def _set_key(monkeypatch):
    monkeypatch.setenv("DISCOVERY_API_KEY", KEY)


def test_requires_key(env):
    client, _ = env
    assert client.post("/api/discover", json={"institution": "MIT"}).status_code == 401
    assert client.post("/api/discover", json={"institution": "MIT"},
                       headers={"X-Discovery-Key": "wrong"}).status_code == 401
    assert client.get("/api/discover/1").status_code == 401


def test_fails_closed_without_secret(env, monkeypatch):
    client, _ = env
    monkeypatch.delenv("DISCOVERY_API_KEY", raising=False)
    assert client.post("/api/discover", json={"institution": "MIT"}, headers=HEADERS).status_code == 401


def test_enqueue_and_dedup_live(env):
    client, factory = env
    r1 = client.post("/api/discover", json={"institution": "Enqueue University", "topic": "ai"},
                     headers=HEADERS)
    assert r1.status_code == 200
    body = r1.json()
    assert body["status"] == "queued" and body["cached"] is False and body["run_id"] is None
    job_id = body["job_id"]

    # Same seed again while live → returns the same job, no duplicate.
    r2 = client.post("/api/discover", json={"institution": "enqueue university", "topic": "AI"},
                     headers=HEADERS)
    assert r2.json()["job_id"] == job_id
    with factory() as s:
        h = seed_hash("Enqueue University", "ai")
        assert len(s.scalars(select(DiscoveryJob).where(DiscoveryJob.seed_hash == h)).all()) == 1

    # Status endpoint works (gated).
    st = client.get(f"/api/discover/{job_id}", headers=HEADERS)
    assert st.status_code == 200 and st.json()["status"] == "queued"
    assert client.get("/api/discover/999999", headers=HEADERS).status_code == 404


def test_cache_hit_returns_existing_run(env):
    client, factory = env
    published = None
    with factory() as s:
        published = get_published_run_id(s)
        job = DiscoveryJob(seed=build_seed("Cached University", None),
                           seed_hash=seed_hash("Cached University", None), scrape=False,
                           status="succeeded", stage="done", run_id=published)
        s.add(job)
        s.commit()
    r = client.post("/api/discover", json={"institution": "Cached University"}, headers=HEADERS)
    body = r.json()
    assert body["cached"] is True and body["status"] == "succeeded" and body["run_id"] == published


def test_rejects_empty_institution(env):
    client, _ = env
    assert client.post("/api/discover", json={"institution": ""}, headers=HEADERS).status_code == 422
    assert client.post("/api/discover", json={}, headers=HEADERS).status_code == 422
