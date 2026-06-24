"""Load ImportRows into Postgres and serve the graph back from it (main-thread integration code).

This is the DB-backed counterpart to the pure importer/serializer. It implements the weighted-edge
strategy from SCHEMA.md: parallel COAUTHORED_WITH edges collapse to ONE row with weight=count on the
way in, and expand by weight on the way out — so the DB-backed /api/graph/data reproduces the
existing graph (same nodes, same link multiset). Idempotent: re-loading the same rows inserts no
duplicates (ON CONFLICT DO NOTHING against the PKs / edge_uq constraint).
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.repopulation.models.edges import Edge
from backend.repopulation.models.nodes import Node, Relevance, RepopulationRun
from backend.repopulation.models.provenance import SourceRecord
from backend.repopulation.serializers.graph_data import serialize_graph


def load_import_rows(session: Session, rows: dict) -> dict:
    """Idempotent insert of ImportRows (SCHEMA.md §1). Returns row counts after load."""
    run_id = {r["key"]: _insert_run(session, r) for r in rows["runs"]}
    src_id = {s["key"]: _insert_source(session, s, run_id) for s in rows["source_records"]}

    for n in rows["nodes"]:
        session.execute(
            pg_insert(Node)
            .values(
                id=n["id"], kind=n["kind"], name=n["name"], val=n["val"],
                orcid=n["orcid"], openalex_id=n["openalex_id"], ror=n["ror"],
                normalized_name=n["normalized_name"], attributes=n["attributes"],
                ai_description=n["ai_description"], description_model=n["description_model"],
                confidence=n["confidence"], source_record_id=src_id.get(n["source_record_key"]),
            )
            .on_conflict_do_nothing(index_elements=["id"])
        )

    # Collapse parallel edges to one weighted row (weight = #occurrences), preserving first-seen meta.
    weight: Counter = Counter()
    first: dict = {}
    for e in rows["edges"]:
        key = (e["src_id"], e["dst_id"], e["type"])
        first.setdefault(key, e)
        weight[key] += 1
    for key, e in first.items():
        session.execute(
            pg_insert(Edge)
            .values(
                src_id=key[0], dst_id=key[1], type=key[2], weight=float(weight[key]),
                directed=e["directed"], attributes=e["attributes"], confidence=e["confidence"],
                source_record_id=src_id.get(e["source_record_key"]),
            )
            .on_conflict_do_nothing(constraint="edge_uq")
        )

    for rel in rows["relevance"]:
        session.execute(
            pg_insert(Relevance)
            .values(
                node_id=rel["node_id"], run_id=run_id[rel["run_key"]],
                score=rel["score"], components=rel["components"],
            )
            .on_conflict_do_nothing(index_elements=["node_id", "run_id"])
        )

    session.commit()
    return {
        "nodes": session.scalar(select(func.count()).select_from(Node)),
        "edges": session.scalar(select(func.count()).select_from(Edge)),
        "relevance": session.scalar(select(func.count()).select_from(Relevance)),
        "source_records": session.scalar(select(func.count()).select_from(SourceRecord)),
    }


def _insert_run(session: Session, r: dict) -> int:
    """Get-or-create by seed so re-importing the same static data is idempotent (the legacy
    run is a singleton). A genuinely new repopulation passes a distinct seed → a new run."""
    existing = session.scalar(
        select(RepopulationRun).where(RepopulationRun.seed == r["seed"])
    )
    if existing is not None:
        return existing.id
    run = RepopulationRun(seed=r["seed"], status=r["status"])
    session.add(run)
    session.flush()
    return run.id


def _insert_source(session: Session, s: dict, run_id: dict) -> int:
    """Get-or-create the (source, run) source_record so a re-import doesn't duplicate it."""
    rid = run_id.get(s["run_key"])
    existing = session.scalar(
        select(SourceRecord).where(
            SourceRecord.source == s["source"], SourceRecord.run_id == rid
        )
    )
    if existing is not None:
        return existing.id
    sr = SourceRecord(
        source=s["source"], source_url=s["source_url"], confidence=s["confidence"],
        evidence=s["evidence"], run_id=rid, raw_s3_key=s["raw_s3_key"],
    )
    session.add(sr)
    session.flush()
    return sr.id


def graph_from_db(session: Session) -> dict:
    """Reproduce the frontend graph from Postgres: query nodes/edges, expand weighted
    COAUTHORED_WITH back to parallel paper links, serialize. Legacy influence comes from
    node.attributes (verbatim), so the relevance map is unused here."""
    node_rows = [
        {
            "id": n.id, "kind": n.kind, "name": n.name, "val": n.val,
            "attributes": n.attributes or {}, "ai_description": n.ai_description,
        }
        for n in session.scalars(select(Node)).all()
    ]
    edge_rows: list[dict] = []
    for e in session.scalars(select(Edge)).all():
        reps = int(e.weight) if e.type == "COAUTHORED_WITH" else 1
        edge_rows.extend(
            {"src_id": e.src_id, "dst_id": e.dst_id, "type": e.type} for _ in range(reps)
        )
    return serialize_graph(node_rows, edge_rows, {})
