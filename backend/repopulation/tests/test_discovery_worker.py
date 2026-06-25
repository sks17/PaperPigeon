"""P5: discovery worker + DB budget + claim-safety (pgserver, stub clients).

Drives the worker against an in-process Postgres with stubbed ROR/OpenAlex/Embeddings/LLM (no
network): a queued job is claimed (FOR UPDATE SKIP LOCKED), the real pipeline runs, the job +
researchers + descriptions land, and the job is marked succeeded. The failure path asserts a thrown
pipeline sets the job failed AND clears a stuck 'running' run (the bug fix). Also unit-tests
DbDailyBudget's atomic cap. Mirrors the pgserver fixture in tests/test_run_repopulation.py.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

pytest.importorskip("pgserver")

import pgserver  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.repopulation import worker as worker_mod  # noqa: E402
from backend.repopulation.clients.budget import BudgetExceeded, DbDailyBudget  # noqa: E402
from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import graph_from_db, load_import_rows  # noqa: E402
from backend.repopulation.models.discovery_job import DiscoveryJob  # noqa: E402
from backend.repopulation.models.nodes import RepopulationRun  # noqa: E402
from backend.repopulation.sources.ror_parse import RorOrganization  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "public" / "graph_cache.json"

ROR_ORG = RorOrganization(id="https://ror.org/demo", name="Demo University", country="US")
OPENALEX_INST = {"id": "https://openalex.org/Idemo", "display_name": "Demo University"}
RAW_AUTHORS = [
    {
        "id": "https://openalex.org/DA1", "display_name": "Maya Chen",
        "ids": {"orcid": "https://orcid.org/0000-0000-0000-1001"},
        "last_known_institution": {"id": "https://openalex.org/Idemo", "ror": "https://ror.org/demo",
                                   "display_name": "Demo University"},
        "works_count": 30, "summary_stats": {"h_index": 12},
        "topics": [{"id": "https://openalex.org/T1", "display_name": "Graph Neural Networks", "score": 0.9}],
        "recent_works": [{"id": "https://openalex.org/W1", "title": "Scalable GNNs", "publication_year": 2024,
                          "topics": [{"id": "https://openalex.org/T1", "display_name": "Graph Neural Networks"}]}],
    },
    {
        "id": "https://openalex.org/DA2", "display_name": "Liam Okafor",
        "ids": {"orcid": "https://orcid.org/0000-0000-0000-1002"},
        "last_known_institution": {"id": "https://openalex.org/Idemo", "ror": "https://ror.org/demo",
                                   "display_name": "Demo University"},
        "works_count": 12, "summary_stats": {"h_index": 5},
        "topics": [{"id": "https://openalex.org/T1", "display_name": "Graph Neural Networks", "score": 0.6}],
        "recent_works": [{"id": "https://openalex.org/W1", "title": "Scalable GNNs", "publication_year": 2024}],
    },
]


class _StubRor:
    def resolve(self, name):
        return ROR_ORG


class _StubOpenAlex:
    def __init__(self, *, fail=False):
        self._fail = fail

    def get_institution_by_ror(self, ror_id):
        if self._fail:
            raise RuntimeError("openalex boom")
        return OPENALEX_INST

    def discover_authors(self, institution_id, *, from_year, max_author_pages, max_work_pages):
        return [json.loads(json.dumps(a)) for a in RAW_AUTHORS]


class _StubEmbeddings:
    model = "stub-embed"

    def embed_texts(self, texts):
        out = []
        for i, t in enumerate(texts):
            v = [0.0] * 1536
            v[(len(t) + i) % 1536] = 1.0
            v[0] = 0.5
            out.append(v)
        return out


class _StubLlm:
    model = "stub-llm"

    def complete_json(self, system, user, *, model=None):
        return {"summary": "Works on graph learning, grounded in the cited evidence.",
                "evidence": [1], "confidence": 0.9}


def _stub_clients(*, openalex_fail=False):
    return {
        "http": None, "budget": None, "robots": None,
        "ror": _StubRor(),
        "openalex": _StubOpenAlex(fail=openalex_fail),
        "embeddings": _StubEmbeddings(),
        "llm": _StubLlm(),
    }


@pytest.fixture(scope="module")
def factory(tmp_path_factory):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for m in migration_files():
            srv.psql(m.read_text(encoding="utf-8"))
        f = make_session_factory(make_engine(srv.get_uri()))
        with f() as s:
            load_import_rows(s, cache_to_rows(json.loads(CACHE.read_text("utf-8"))))
        yield f
    finally:
        srv.cleanup()


def _enqueue(factory, institution, topic, scrape=False):
    from backend.repopulation.discovery_service import build_seed, seed_hash
    with factory() as s:
        job = DiscoveryJob(seed=build_seed(institution, topic), seed_hash=seed_hash(institution, topic),
                           scrape=scrape, status="queued", stage="queued")
        s.add(job)
        s.commit()
        return job.id


# ── DbDailyBudget unit ──────────────────────────────────────────────────────
def test_db_budget_caps_atomically(factory):
    today = datetime.date(2026, 6, 24)
    b = DbDailyBudget(factory, 1.0, today)
    b.charge(0.6, "a")
    assert round(b.spent, 4) == 0.6
    with pytest.raises(BudgetExceeded):
        b.charge(0.6, "b")  # 0.6 + 0.6 > 1.0
    assert round(b.spent, 4) == 0.6  # not incremented on the blocked charge
    b.charge(0.4, "c")
    assert round(b.spent, 4) == 1.0


def test_db_budget_uncapped_never_blocks(factory):
    b = DbDailyBudget(factory, None, datetime.date(2026, 1, 1))
    b.charge(999.0, "big")
    assert b.spent == 999.0
    assert b.remaining() is None


# ── worker happy path ───────────────────────────────────────────────────────
def test_worker_processes_job(factory, monkeypatch):
    monkeypatch.setattr(worker_mod, "_build_clients", lambda sf: _stub_clients())
    job_id = _enqueue(factory, "Demo University", "graph learning")

    with factory() as s:
        claimed = worker_mod.claim_job(s)
    assert claimed == job_id
    worker_mod.process_job(factory, job_id)

    with factory() as s:
        job = s.get(DiscoveryJob, job_id)
        assert job.status == "succeeded" and job.stage == "done" and job.run_id is not None
        run_graph = graph_from_db(s, run_id=job.run_id)
        researchers = [n for n in run_graph["nodes"] if n["type"] == "researcher"]
        assert {n["name"] for n in researchers} == {"Maya Chen", "Liam Okafor"}
        assert all(n["about"] for n in researchers)  # grounded descriptions present
        # published (legacy) graph untouched
        assert len(graph_from_db(s)["nodes"]) == 323


def test_claim_is_exclusive(factory, monkeypatch):
    monkeypatch.setattr(worker_mod, "_build_clients", lambda sf: _stub_clients())
    _enqueue(factory, "Solo University", None)
    with factory() as s1:
        first = worker_mod.claim_job(s1)  # claims + commits 'running'
    assert first is not None
    with factory() as s2:
        second = worker_mod.claim_job(s2)  # no more queued jobs
    assert second is None


# ── worker failure path (+ stuck-running fix) ───────────────────────────────
def test_worker_failure_marks_failed_and_clears_stuck_run(factory, monkeypatch):
    monkeypatch.setattr(worker_mod, "_build_clients", lambda sf: _stub_clients(openalex_fail=True))
    job_id = _enqueue(factory, "Broken University", None)
    with factory() as s:
        worker_mod.claim_job(s)
    # Simulate a run left stuck in 'running' for this institution (the historical bug).
    with factory() as s:
        s.add(RepopulationRun(seed={"institution": "Broken University"}, status="running"))
        s.commit()

    try:
        worker_mod.process_job(factory, job_id)
        raised = False
    except Exception as exc:  # noqa: BLE001
        worker_mod.handle_failure(factory, job_id, exc)
        raised = True
    assert raised

    with factory() as s:
        job = s.get(DiscoveryJob, job_id)
        assert job.status == "failed" and job.error
        stuck = s.scalars(
            select(RepopulationRun).where(RepopulationRun.seed["institution"].astext == "Broken University")
        ).all()
        assert stuck and all(r.status == "failed" for r in stuck)  # no longer stuck in 'running'
