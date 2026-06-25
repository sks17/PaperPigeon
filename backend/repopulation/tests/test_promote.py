"""P4: promote_descriptions — deliberately enrich the published graph, preserving existing text.

Boots pgserver, loads the legacy graph (published), forces one legacy researcher to have an empty
`about` and another to have an existing one, then loads a small source run whose researchers match
those two by name and carry grounded descriptions. Asserts: the empty one is FILLED; the existing one
is PRESERVED by default and REPLACED only with overwrite=True; promotion is idempotent and tagged
`promoted:<model>`.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pgserver")

import pgserver  # noqa: E402
from sqlalchemy import select  # noqa: E402

from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import apply_description_updates, load_import_rows  # noqa: E402
from backend.repopulation.models.nodes import Node, RepopulationRun  # noqa: E402
from backend.repopulation.promote import promote_descriptions  # noqa: E402

ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / "public" / "graph_cache.json"
GEN_AT = "2026-06-24T00:00:00+00:00"


SRC1, SRC2 = "https://openalex.org/SRC1", "https://openalex.org/SRC2"
SRC_EVIDENCE = [{"id": 1, "kind": "topics", "text": "Research topics: X."}]


def _src_node(nid, name):
    return {"id": nid, "kind": "researcher", "name": name, "val": 1, "orcid": None,
            "openalex_id": nid, "ror": None, "normalized_name": name.lower(),
            "attributes": {"papers": [], "tags": []}, "ai_description": None,
            "description_model": None, "confidence": 1.0, "source_record_key": "oa"}


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory):
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")))
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        factory = make_session_factory(make_engine(srv.get_uri()))
        with factory() as session:
            load_import_rows(session, cache_to_rows(json.loads(CACHE.read_text(encoding="utf-8"))))
            # Two legacy researchers: one forced empty (fill target), one with an existing bio.
            legacy = session.scalars(
                select(Node).where(Node.kind == "researcher").order_by(Node.id).limit(2)
            ).all()
            empty_node, filled_node = legacy[0], legacy[1]
            empty_node.ai_description = None
            empty_node.description_model = None
            filled_node.ai_description = "EXISTING LEGACY BIO"
            filled_node.description_model = "legacy_dynamodb"
            session.commit()
            names = (empty_node.name, filled_node.name, empty_node.id, filled_node.id)

            # Source run: researchers matching those two names, with grounded descriptions.
            load_import_rows(session, {
                "runs": [{"key": "r", "seed": {"institution": "Test U", "topic": "x"},
                          "status": "succeeded"}],
                "source_records": [{"key": "oa", "source": "openalex", "source_url": None,
                                    "confidence": None, "evidence": "oa", "run_key": "r",
                                    "raw_s3_key": None}],
                "nodes": [_src_node(SRC1, names[0]), _src_node(SRC2, names[1])],
                "edges": [], "relevance": [],
            })
            # Describe the source nodes the way describe_run would (sets generated_at + evidence).
            apply_description_updates(session, [
                {"node_id": SRC1, "ai_description": "Grounded bio for the empty one.",
                 "description_model": "stub-llm", "description_generated_at": GEN_AT,
                 "description_evidence": SRC_EVIDENCE},
                {"node_id": SRC2, "ai_description": "Grounded bio for the filled one.",
                 "description_model": "stub-llm", "description_generated_at": GEN_AT,
                 "description_evidence": SRC_EVIDENCE},
            ])
        run_id = None
        with factory() as session:
            run_id = session.scalar(
                select(RepopulationRun.id).where(RepopulationRun.seed["topic"].astext == "x")
            )
        yield factory, run_id, names
    finally:
        srv.cleanup()


def test_fills_empty_preserves_existing_by_default(env) -> None:
    factory, run_id, (_, _, empty_id, filled_id) = env
    with factory() as s:
        result = promote_descriptions(s, run_id)  # default: no overwrite
    with factory() as s:
        empty = s.get(Node, empty_id)
        filled = s.get(Node, filled_id)
    assert result["matched"] == 2
    assert result["promoted"] == 1 and result["skipped_existing"] == 1
    # Empty legacy researcher filled with the grounded bio, tagged promoted:<model>.
    assert empty.ai_description == "Grounded bio for the empty one."
    assert empty.description_model == "promoted:stub-llm"
    assert empty.description_generated_at is not None
    assert empty.description_evidence == SRC_EVIDENCE  # grounding carried with the promotion
    # Existing legacy bio preserved.
    assert filled.ai_description == "EXISTING LEGACY BIO"
    assert filled.description_model == "legacy_dynamodb"


def test_idempotent(env) -> None:
    factory, run_id, _ = env
    with factory() as s:
        second = promote_descriptions(s, run_id)  # already promoted/preserved -> nothing new
    assert second["promoted"] == 0
    assert second["skipped_existing"] == 2


def test_overwrite_replaces_existing(env) -> None:
    factory, run_id, (_, _, _, filled_id) = env
    with factory() as s:
        result = promote_descriptions(s, run_id, overwrite=True)
    with factory() as s:
        filled = s.get(Node, filled_id)
    assert result["promoted"] == 2  # both replaced under overwrite
    assert filled.ai_description == "Grounded bio for the filled one."
    assert filled.description_model == "promoted:stub-llm"
