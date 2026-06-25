"""P4: end-to-end grounded-description orchestration with a STUB LLM + pgserver.

Drives describe_run over a small repopulation run loaded into an in-process Postgres, injecting a
stub LLM (canned grounded JSON — no network) and stub embeddings (so the pgvector neighbour path
runs). Asserts: descriptions land on the run's researchers; the published legacy snapshot is
untouched (counts + its `about` text preserved); pgvector "related" evidence reaches the prompt;
re-running is idempotent; ungrounded/hallucinated descriptions are quarantined not written; and the
loader's UPDATE path never overwrites a legacy DynamoDB description.

Mirrors the pgserver fixture pattern in tests/test_run_repopulation.py. Run by the main thread
(`python -m pytest -q` from the project root).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pgserver")

import pgserver  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.describe_run import describe_run  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import (  # noqa: E402
    apply_description_updates,
    graph_from_db,
    load_import_rows,
)
from backend.repopulation.models.membership import Quarantine  # noqa: E402
from backend.repopulation.models.nodes import Node, RepopulationRun  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "public" / "graph_cache.json"
LEGACY_COUNTS = (323, 1043)
EMBED_MODEL = "stub-embed"
LLM_MODEL = "stub-llm"


# ── stubs ─────────────────────────────────────────────────────────────────────
class _StubLlm:
    """Canned grounded JSON; records the prompts it was asked to complete."""

    def __init__(self, responder=None, *, model: str = LLM_MODEL) -> None:
        self.model = model
        self._responder = responder or (
            lambda system, user: {"summary": "Grounded summary.", "evidence": [1], "confidence": 0.9}
        )
        self.calls: list[tuple[str, str]] = []

    def complete_json(self, system: str, user: str, *, model: str | None = None) -> dict:
        self.calls.append((system, user))
        return self._responder(system, user)


def _vec(seed: int) -> list[float]:
    """A deterministic, non-zero 1536-d vector (matches the repop.embedding pgvector column)."""
    v = [0.0] * 1536
    v[seed % 1536] = 1.0
    v[(seed * 7 + 1) % 1536] += 0.5
    return v


# ── a tiny, fully-specified repopulation run (ImportRows) ───────────────────────
def _repop_rows(run_key: str, topic: str, *, with_embeddings: bool) -> dict:
    seed = {"institution": "Test University", "topic": topic, "keywords": []}
    src = {
        "key": "oa", "source": "openalex", "source_url": None, "confidence": None,
        "evidence": "openalex", "run_key": run_key, "raw_s3_key": None,
    }

    def node(nid, kind, name, val, attrs):
        return {
            "id": nid, "kind": kind, "name": name, "val": val,
            "orcid": None, "openalex_id": nid, "ror": None, "normalized_name": name.lower(),
            "attributes": attrs, "ai_description": None, "description_model": None,
            "confidence": 1.0, "source_record_key": "oa",
        }

    def edge(src_id, dst_id, type_, weight):
        return {
            "src_id": src_id, "dst_id": dst_id, "type": type_, "weight": weight,
            "directed": True, "attributes": {}, "confidence": 1.0, "source_record_key": "oa",
        }

    a1 = f"https://openalex.org/{run_key}-A1"
    a2 = f"https://openalex.org/{run_key}-A2"
    inst = f"https://openalex.org/{run_key}-I100"
    rows = {
        "runs": [{"key": run_key, "seed": seed, "status": "succeeded"}],
        "source_records": [src],
        "nodes": [
            node(inst, "institution", "Test University", 3, {"country": "US"}),
            node(a1, "researcher", "Alice Example", 1, {
                "papers": [{"title": "Graphs at Scale", "year": 2024, "document_id": "W1", "tags": []}],
                "tags": ["Graph Theory"], "works_count": 42,
            }),
            node(a2, "researcher", "Bob Example", 1, {
                "papers": [{"title": "Network Flows", "year": 2023, "document_id": "W2", "tags": []}],
                "tags": ["Networks"], "works_count": 17,
            }),
        ],
        "edges": [
            edge(a1, inst, "AFFILIATED_WITH", 1.0),
            edge(a2, inst, "AFFILIATED_WITH", 1.0),
            edge(a1, a2, "COAUTHORED_WITH", 1.0),
        ],
        "relevance": [],
    }
    if with_embeddings:
        rows["embeddings"] = [
            {"node_id": a1, "model": EMBED_MODEL, "embedding": _vec(1)},
            {"node_id": a2, "model": EMBED_MODEL, "embedding": _vec(2)},
        ]
    return rows, a1, a2


@pytest.fixture(scope="module")
def session_factory(tmp_path_factory: pytest.TempPathFactory):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        factory = make_session_factory(make_engine(srv.get_uri()))
        with factory() as session:
            load_import_rows(session, cache_to_rows(json.loads(CACHE.read_text(encoding="utf-8"))))
        yield factory
    finally:
        srv.cleanup()


def _load(factory, run_key, topic, *, with_embeddings):
    """Load one tiny repopulation run; return its (run_id, a1_id, a2_id). The seed topic is unique
    per run so tests stay independent of each other (each resolves its own run id)."""
    rows, a1, a2 = _repop_rows(run_key, topic, with_embeddings=with_embeddings)
    with factory() as session:
        load_import_rows(session, rows)
        run_id = session.scalar(
            select(RepopulationRun.id).where(RepopulationRun.seed["topic"].astext == topic)
        )
    return run_id, a1, a2


GEN_AT = "2026-06-24T00:00:00+00:00"


def test_describes_run_researchers_and_preserves_legacy(session_factory) -> None:
    run_id, a1, a2 = _load(session_factory, "rA", "graphs-A", with_embeddings=True)

    with session_factory() as s:
        default_before = graph_from_db(s)
    assert (len(default_before["nodes"]), len(default_before["links"])) == LEGACY_COUNTS
    legacy_about_before = [n["id"] for n in default_before["nodes"] if n.get("about")]
    assert legacy_about_before  # legacy graph carries DynamoDB `about` text we must preserve

    llm = _StubLlm()
    with session_factory() as s:
        summary = describe_run(
            s, run_id, llm=llm, generated_at=GEN_AT, model=LLM_MODEL,
            embedding_model=EMBED_MODEL,
        )

    assert summary["described"] == 2 and summary["updated"] == 2
    assert summary["quarantined"] == 0

    # Run snapshot researchers now carry grounded `about`; the institution node isn't rendered.
    with session_factory() as s:
        run_graph = graph_from_db(s, run_id=run_id)
        default_after = graph_from_db(s)
    researchers = [n for n in run_graph["nodes"] if n["type"] == "researcher"]
    assert {n["id"] for n in researchers} == {a1, a2}
    assert all(n["about"] == "Grounded summary." for n in researchers)

    # Published legacy snapshot is byte-for-byte unchanged (counts + its `about` text).
    assert default_after == default_before

    # pgvector "related" evidence reached the prompt (proves retrieval ran).
    joined = "\n".join(user for _, user in llm.calls)
    assert "Works in a similar area to" in joined


def test_idempotent_rerun_is_noop(session_factory) -> None:
    run_id, _, _ = _load(session_factory, "rB", "graphs-B", with_embeddings=True)
    llm = _StubLlm()
    with session_factory() as s:
        first = describe_run(s, run_id, llm=llm, generated_at=GEN_AT, model=LLM_MODEL,
                             embedding_model=EMBED_MODEL)
    with session_factory() as s:
        before = graph_from_db(s, run_id=run_id)
        second = describe_run(s, run_id, llm=llm, generated_at="2026-07-01T00:00:00+00:00",
                              model=LLM_MODEL, embedding_model=EMBED_MODEL)
        after = graph_from_db(s, run_id=run_id)
    assert first["described"] == 2
    assert second["candidates"] == 0 and second["described"] == 0 and second["updated"] == 0
    assert after == before


def test_works_without_embeddings(session_factory) -> None:
    run_id, _, _ = _load(session_factory, "rC", "graphs-C", with_embeddings=False)
    llm = _StubLlm()
    with session_factory() as s:
        summary = describe_run(s, run_id, llm=llm, generated_at=GEN_AT, model=LLM_MODEL,
                               embedding_model=None)
        run_graph = graph_from_db(s, run_id=run_id)
    # Grounds on stored facts (papers/topics/coauthor) even with no pgvector context.
    assert summary["described"] == 2
    assert all(n["about"] for n in run_graph["nodes"] if n["type"] == "researcher")
    joined = "\n".join(user for _, user in llm.calls)
    assert "Works in a similar area to" not in joined  # no embeddings -> no related evidence


def test_ungrounded_description_is_quarantined(session_factory) -> None:
    run_id, _, _ = _load(session_factory, "rD", "graphs-D", with_embeddings=False)
    # Cite an evidence id (99) that was never shown -> hallucinated -> ungrounded.
    llm = _StubLlm(lambda s, u: {"summary": "Made up.", "evidence": [99], "confidence": 0.9})
    with session_factory() as s:
        summary = describe_run(s, run_id, llm=llm, generated_at=GEN_AT, model=LLM_MODEL)
        run_graph = graph_from_db(s, run_id=run_id)
        q = s.scalar(
            select(func.count()).select_from(Quarantine)
            .where(Quarantine.run_id == run_id, Quarantine.kind == "description")
        )
    assert summary["described"] == 0 and summary["quarantined"] == 2
    assert q == 2
    assert all(n["about"] is None for n in run_graph["nodes"] if n["type"] == "researcher")


def test_low_confidence_is_quarantined(session_factory) -> None:
    run_id, _, _ = _load(session_factory, "rE", "graphs-E", with_embeddings=False)
    llm = _StubLlm(lambda s, u: {"summary": "Unsure.", "evidence": [1], "confidence": 0.2})
    with session_factory() as s:
        summary = describe_run(s, run_id, llm=llm, generated_at=GEN_AT, model=LLM_MODEL,
                               min_confidence=0.5)
    assert summary["described"] == 0 and summary["quarantined"] == 2


def test_describes_new_lab_and_excludes_legacy_lab(session_factory) -> None:
    # A repop run containing a NEW scraped lab + a MERGED legacy lab (reusing a published lab id,
    # as build_lab_rows does). describe_run must describe the new lab and leave the legacy one alone.
    run_key, topic = "rLab", "labs-A"
    with session_factory() as s:
        legacy_lab = s.scalars(select(Node).where(Node.kind == "lab").limit(1)).first()
        assert legacy_lab is not None
        legacy_lab_id, legacy_about_before = legacy_lab.id, legacy_lab.ai_description

    a1 = f"https://openalex.org/{run_key}-A1"
    new_lab = f"lab:{run_key}-I100:vision-lab"
    seed = {"institution": "Test University", "topic": topic, "keywords": []}
    src = {"key": "oa", "source": "openalex", "source_url": None, "confidence": None,
           "evidence": "scrape", "run_key": run_key, "raw_s3_key": None}

    def node(nid, kind, name, val, attrs, *, ai=None, dmodel=None):
        return {"id": nid, "kind": kind, "name": name, "val": val, "orcid": None,
                "openalex_id": None, "ror": None, "normalized_name": name.lower(),
                "attributes": attrs, "ai_description": ai, "description_model": dmodel,
                "confidence": 1.0, "source_record_key": "oa"}

    def edge(s_id, d_id, t):
        return {"src_id": s_id, "dst_id": d_id, "type": t, "weight": 1.0, "directed": True,
                "attributes": {}, "confidence": 1.0, "source_record_key": "oa"}

    rows = {
        "runs": [{"key": run_key, "seed": seed, "status": "succeeded"}],
        "source_records": [src],
        "nodes": [
            node(a1, "researcher", "Alice Example", 1,
                 {"papers": [], "tags": ["Computer Vision"], "works_count": 5}),
            node(new_lab, "lab", "Vision Lab", 2,
                 {"description": "We study computer vision and robot perception.",
                  "pi": "Dr. Vee", "research_areas": ["computer vision", "robotics"],
                  "faculty": [a1], "url": "http://example.test/vision"},
                 ai="Raw scraped blurb.", dmodel="scrape"),
            # Merged legacy lab: id reused -> loader keeps the node (DO NOTHING) but adds run membership.
            node(legacy_lab_id, "lab", "Legacy Lab", 2, {}),
        ],
        "edges": [edge(a1, new_lab, "MEMBER_OF"), edge(a1, legacy_lab_id, "MEMBER_OF")],
        "relevance": [],
    }
    with session_factory() as s:
        load_import_rows(s, rows)
        run_id = s.scalar(
            select(RepopulationRun.id).where(RepopulationRun.seed["topic"].astext == topic)
        )

    llm = _StubLlm()  # cites evidence id 1 (the lab's self-description / the researcher's topics)
    with session_factory() as s:
        summary = describe_run(s, run_id, llm=llm, generated_at=GEN_AT, model=LLM_MODEL,
                               kinds=("researcher", "lab"))

    with session_factory() as s:
        new = s.get(Node, new_lab)
        legacy = s.get(Node, legacy_lab_id)
        alice = s.get(Node, a1)

    # New lab + researcher described; legacy lab excluded (it belongs to the published run).
    assert summary["described"] == 2
    assert new.description_model == LLM_MODEL and new.ai_description == "Grounded summary."
    assert new.description_evidence  # grounding persisted
    assert alice.description_model == LLM_MODEL
    assert legacy.description_model != LLM_MODEL and legacy.ai_description == legacy_about_before

    # Lab evidence (self-description + member) reached the prompt.
    joined = "\n".join(user for _, user in llm.calls)
    assert "We study computer vision" in joined and "Lab member: Alice Example" in joined


def test_apply_updates_never_overwrites_legacy(session_factory) -> None:
    # Pick a real legacy researcher with a non-empty `about` and try to overwrite it directly.
    with session_factory() as s:
        legacy = s.scalars(
            select(Node).where(Node.description_model == "legacy_dynamodb").limit(1)
        ).first()
        assert legacy is not None
        original = legacy.ai_description
        node_id = legacy.id

    update = {
        "node_id": node_id, "ai_description": "HIJACKED", "description_model": "stub-llm",
        "description_generated_at": GEN_AT, "description_evidence": [],
    }
    with session_factory() as s:
        updated = apply_description_updates(s, [update])
    with session_factory() as s:
        after = s.get(Node, node_id)
    assert updated == 0
    assert after.ai_description == original
    assert after.description_model == "legacy_dynamodb"
