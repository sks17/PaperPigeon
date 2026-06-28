"""Tests for committed example-run seeding (backend/repopulation/examples/seed.py).

Seeding an example snapshot must: create a selectable run with its nodes/edges/descriptions,
leave the published legacy graph untouched (isolation), and be idempotent (re-seeding is a no-op).
Uses a small synthetic artifact via monkeypatch so it doesn't depend on the large committed JSON.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pgserver")

import pgserver  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.repopulation import examples  # noqa: E402
from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.examples import seed as seed_mod  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import graph_from_db, load_import_rows  # noqa: E402
from backend.repopulation.models.nodes import RepopulationRun  # noqa: E402
from backend.repopulation.reads import lab_detail, node_description  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE = PROJECT_ROOT / "public" / "graph_cache.json"

EXAMPLE_SEED = {"institution": "Example University", "topic": None, "keywords": []}
LAB_ID = "lab:estimated:I1:A1"


def _artifact() -> dict:
    def node(nid, kind, name, val, attrs, desc=None, model=None, conf=1.0):
        return {"id": nid, "kind": kind, "name": name, "val": val, "orcid": None,
                "openalex_id": nid, "ror": None, "normalized_name": name.lower(),
                "attributes": attrs, "ai_description": desc, "description_model": model,
                "confidence": conf, "source_record_key": "openalex"}

    def edge(src, dst, type_, weight=1.0, conf=1.0):
        return {"src_id": src, "dst_id": dst, "type": type_, "weight": weight,
                "directed": True, "attributes": {}, "confidence": conf,
                "source_record_key": "openalex"}

    nodes = [
        node("I1", "institution", "Example University", 3, {"country": "CA"}),
        node("A1", "researcher", "Ada Lovelace", 1, {"tags": ["computing"]},
             desc="Ada works on computing.", model="example-model"),
        node("A2", "researcher", "Alan Turing", 1, {"tags": ["computing"]}),
        node(LAB_ID, "lab", "Ada Lovelace Group", 2,
             {"estimated": True, "method": "coauthorship", "pi": "Ada Lovelace",
              "faculty": ["A1", "A2"], "research_areas": ["computing"]}, conf=0.6),
    ]
    edges = [
        edge("A1", "A2", "COAUTHORED_WITH", weight=3.0),
        edge("A1", LAB_ID, "MEMBER_OF", conf=0.6),
        edge("A2", LAB_ID, "MEMBER_OF", conf=0.6),
    ]
    return {
        "institution": "Example University",
        "import_rows": {
            "runs": [{"key": "example", "seed": EXAMPLE_SEED, "status": "succeeded"}],
            "source_records": [{"key": "openalex", "source": "openalex", "source_url": None,
                                "retrieved_at": None, "confidence": None, "evidence": "fixture",
                                "run_key": "example", "raw_s3_key": None}],
            "nodes": nodes, "edges": edges,
            "relevance": [{"node_id": "A1", "run_key": "example", "score": 0.9, "components": {}}],
        },
        "description_updates": [
            {"node_id": "A1", "ai_description": "Ada works on computing.",
             "description_model": "example-model",
             "description_generated_at": "2026-06-27T00:00:00+00:00",
             "description_evidence": [{"id": "A1", "kind": "topics", "text": "computing"}]},
        ],
    }


@pytest.fixture(scope="module")
def session_factory(tmp_path_factory: pytest.TempPathFactory):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        factory = make_session_factory(make_engine(srv.get_uri()))
        with factory() as session:
            load_import_rows(session, cache_to_rows(json.loads(CACHE.read_text("utf-8"))))
        yield factory
    finally:
        srv.cleanup()


@pytest.fixture()
def with_example(tmp_path, monkeypatch):
    path = tmp_path / "example_university.json"
    path.write_text(json.dumps(_artifact()), encoding="utf-8")
    monkeypatch.setattr(seed_mod, "example_files", lambda: [path])
    return path


def test_seed_creates_selectable_run_without_touching_published_graph(session_factory, with_example):
    with session_factory() as session:
        status = seed_mod.seed_example_runs(session)
        assert "seeded" in next(iter(status.values()))

    with session_factory() as session:
        # Published legacy graph is unchanged.
        published = graph_from_db(session)
        assert (len(published["nodes"]), len(published["links"])) == (323, 1043)

        run_id = session.scalar(
            select(RepopulationRun.id).where(RepopulationRun.seed == EXAMPLE_SEED)
        )
        assert run_id is not None
        run_graph = graph_from_db(session, run_id=run_id)
        labs = [n for n in run_graph["nodes"] if n["type"] == "lab"]
        assert labs and labs[0]["id"] == LAB_ID
        # COAUTHORED_WITH weight 3 expands to 3 paper links; 2 MEMBER_OF render as researcher_lab.
        link_types = sorted(link["type"] for link in run_graph["links"])
        assert link_types == ["paper", "paper", "paper", "researcher_lab", "researcher_lab"]


def test_seed_persists_grounded_descriptions_and_lab_detail(session_factory, with_example):
    with session_factory() as session:
        seed_mod.seed_example_runs(session)

    with session_factory() as session:
        desc = node_description(session, "A1")
        assert desc["about"] == "Ada works on computing."
        assert desc["evidence"]  # citations survive the round-trip
        lab = lab_detail(session, LAB_ID)
        assert lab["pi"] == "Ada Lovelace"
        assert {f["id"] for f in lab["faculty"]} == {"A1", "A2"}


def test_seed_is_idempotent(session_factory, with_example):
    with session_factory() as session:
        seed_mod.seed_example_runs(session)
    with session_factory() as session:
        again = seed_mod.seed_example_runs(session)
        assert next(iter(again.values())) == "already-seeded"
