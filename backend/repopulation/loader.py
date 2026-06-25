"""Load ImportRows into Postgres and serve the graph back from it (main-thread integration code).

DB-backed counterpart to the pure importer/serializer:
- weighted-edge strategy (SCHEMA.md): parallel COAUTHORED_WITH collapse to one weighted row on
  write, expand by weight on read;
- run-membership (migration 0002): records which nodes/edges belong to each run's snapshot, and
  auto-publishes the FIRST run loaded (the legacy import) so the default served graph is unchanged
  while later repopulation runs stay invisible until explicitly published;
- idempotent: re-loading the same rows inserts no duplicates.
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from backend.repopulation.descriptions.build_rows import LEGACY_DESCRIPTION_MODEL
from backend.repopulation.models.edges import Edge
from backend.repopulation.models.membership import (
    PUBLISHED_RUN_KEY,
    AppState,
    RunEdge,
    RunNode,
)
from backend.repopulation.models.nodes import Embedding, Node, Relevance, RepopulationRun
from backend.repopulation.models.provenance import SourceRecord
from backend.repopulation.serializers.graph_data import serialize_graph


def load_import_rows(session: Session, rows: dict) -> dict:
    """Idempotent insert of ImportRows (SCHEMA.md §1) + run-membership. Returns row counts."""
    run_id = {r["key"]: _get_or_create_run(session, r) for r in rows["runs"]}
    src_id = {s["key"]: _get_or_create_source(session, s, run_id) for s in rows["source_records"]}
    src_to_run = {s["key"]: run_id.get(s["run_key"]) for s in rows["source_records"]}

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
        rid = src_to_run.get(n["source_record_key"])
        if rid is not None:
            session.execute(
                pg_insert(RunNode).values(run_id=rid, node_id=n["id"]).on_conflict_do_nothing()
            )

    # Collapse parallel edges to one weighted row by SUMMING row weights. This unifies both
    # importers: the legacy importer emits N parallel weight-1.0 rows (sum = N = #joint works),
    # while discovery emits ONE pre-aggregated row carrying its own weight (sum = that weight).
    weight: Counter = Counter()
    first: dict = {}
    for e in rows["edges"]:
        key = (e["src_id"], e["dst_id"], e["type"])
        first.setdefault(key, e)
        weight[key] += e["weight"]
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
        rid = src_to_run.get(e["source_record_key"])
        edge_id = session.scalar(
            select(Edge.id).where(Edge.src_id == key[0], Edge.dst_id == key[1], Edge.type == key[2])
        )
        if rid is not None and edge_id is not None:
            session.execute(
                pg_insert(RunEdge).values(run_id=rid, edge_id=edge_id).on_conflict_do_nothing()
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

    # pgvector embeddings (optional — present only on repopulation runs that embedded nodes).
    for emb in rows.get("embeddings", []):
        session.execute(
            pg_insert(Embedding)
            .values(node_id=emb["node_id"], model=emb["model"], embedding=emb["embedding"])
            .on_conflict_do_nothing(index_elements=["node_id", "model"])
        )

    # Auto-publish the first run ever loaded (the legacy import) so the default view is unchanged;
    # subsequent repopulation runs are NOT auto-published — they stay invisible until publish_run().
    if len(run_id) == 1:
        _maybe_set_initial_published(session, next(iter(run_id.values())))

    session.commit()
    return {
        "nodes": session.scalar(select(func.count()).select_from(Node)),
        "edges": session.scalar(select(func.count()).select_from(Edge)),
        "relevance": session.scalar(select(func.count()).select_from(Relevance)),
        "source_records": session.scalar(select(func.count()).select_from(SourceRecord)),
    }


def apply_description_updates(session: Session, updates: list[dict]) -> int:
    """Write Phase-4 grounded descriptions onto their nodes (the node upsert is DO-NOTHING, so
    descriptions need an explicit UPDATE). Defends snapshot isolation two ways: it NEVER touches a
    legacy-DynamoDB description (so the published legacy graph's `about` is preserved), and it only
    sets the four description fields (ai_description / description_model / description_generated_at /
    description_evidence) — node identity, attributes, and confidence are untouched. Idempotent:
    re-applying identical updates rewrites the same values. Returns the number of rows updated.
    """
    updated = 0
    for u in updates:
        result = session.execute(
            update(Node)
            .where(
                Node.id == u["node_id"],
                # IS DISTINCT FROM keeps a NULL description_model eligible while guarding legacy.
                Node.description_model.is_distinct_from(LEGACY_DESCRIPTION_MODEL),
            )
            .values(
                ai_description=u["ai_description"],
                description_model=u["description_model"],
                description_generated_at=u["description_generated_at"],
                description_evidence=u["description_evidence"],
            )
        )
        updated += result.rowcount or 0
    session.commit()
    return updated


def get_published_run_id(session: Session) -> int | None:
    value = session.scalar(select(AppState.value).where(AppState.key == PUBLISHED_RUN_KEY))
    return int(value) if value is not None else None


def publish_run(session: Session, run_id: int) -> None:
    """Make `run_id` the default served snapshot."""
    session.execute(
        pg_insert(AppState)
        .values(key=PUBLISHED_RUN_KEY, value=str(run_id))
        .on_conflict_do_update(index_elements=["key"], set_={"value": str(run_id)})
    )
    session.commit()


def _maybe_set_initial_published(session: Session, run_id: int) -> None:
    if get_published_run_id(session) is None:
        session.execute(
            pg_insert(AppState)
            .values(key=PUBLISHED_RUN_KEY, value=str(run_id))
            .on_conflict_do_nothing(index_elements=["key"])
        )


def _get_or_create_run(session: Session, r: dict) -> int:
    existing = session.scalar(select(RepopulationRun).where(RepopulationRun.seed == r["seed"]))
    if existing is not None:
        return existing.id
    run = RepopulationRun(seed=r["seed"], status=r["status"])
    session.add(run)
    session.flush()
    return run.id


def _get_or_create_source(session: Session, s: dict, run_id: dict) -> int:
    rid = run_id.get(s["run_key"])
    existing = session.scalar(
        select(SourceRecord).where(SourceRecord.source == s["source"], SourceRecord.run_id == rid)
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


def graph_from_db(session: Session, run_id: int | None = None) -> dict:
    """Reproduce the frontend graph from Postgres for a run snapshot. `run_id=None` serves the
    published run (the legacy import by default); if nothing is published, serves all rows
    (back-compat). Expands weighted COAUTHORED_WITH back to parallel paper links."""
    effective = run_id if run_id is not None else get_published_run_id(session)

    node_q = select(Node)
    edge_q = select(Edge)
    if effective is not None:
        node_q = node_q.join(RunNode, RunNode.node_id == Node.id).where(RunNode.run_id == effective)
        edge_q = edge_q.join(RunEdge, RunEdge.edge_id == Edge.id).where(RunEdge.run_id == effective)

    node_rows = [
        {
            "id": n.id, "kind": n.kind, "name": n.name, "val": n.val,
            "attributes": n.attributes or {}, "ai_description": n.ai_description,
        }
        for n in session.scalars(node_q).all()
    ]
    edge_rows: list[dict] = []
    for e in session.scalars(edge_q).all():
        reps = int(e.weight) if e.type == "COAUTHORED_WITH" else 1
        edge_rows.extend(
            {"src_id": e.src_id, "dst_id": e.dst_id, "type": e.type} for _ in range(reps)
        )
    return serialize_graph(node_rows, edge_rows, {})
