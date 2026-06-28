"""Repopulation run orchestration (main-thread integration code).

Pipeline for one seed: resolve institution (ROR) -> discover authors+works (OpenAlex, budget-aware)
-> build_rows -> compute query-scoped relevance (embeddings optional) -> idempotent upsert. The run
is NOT auto-published — it stays invisible to the default served graph until publish_run().
"""
from __future__ import annotations

import dataclasses

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.repopulation.discovery.build_rows import build_import_rows
from backend.repopulation.loader import load_import_rows
from backend.repopulation.models.nodes import RepopulationRun
from backend.repopulation.relevance.score import score_relevance
from backend.repopulation.sources.openalex_parse import parse_openalex_author

RUN_KEY = "run"
SOURCE_KEYS = {"openalex": "openalex", "ror": "ror", "estimate": "estimate"}


def _researcher_text(node: dict) -> str:
    attrs = node.get("attributes") or {}
    tags = " ".join(attrs.get("tags") or [])
    titles = " ".join(p.get("title") or "" for p in (attrs.get("papers") or []))
    return f"{node.get('name', '')} {tags} {titles}".strip()


def _node_meta(researcher_nodes: list[dict]) -> dict:
    meta = {}
    for node in researcher_nodes:
        attrs = node.get("attributes") or {}
        years = [p["year"] for p in (attrs.get("papers") or []) if p.get("year")]
        meta[node["id"]] = {
            "last_year": max(years) if years else None,
            "volume": attrs.get("works_count") or 0,
        }
    return meta


def run_repopulation(
    session: Session,
    seed: dict,
    *,
    ror,
    openalex,
    current_year: int,
    embeddings=None,
    max_author_pages: int = 2,
    max_work_pages: int = 5,
) -> dict:
    name = seed["institution"]
    org = ror.resolve(name)
    if org is None:
        raise ValueError(f"ROR could not resolve institution: {name!r}")

    institution = openalex.get_institution_by_ror(org.id)
    institution_id = institution.get("id")
    if not institution_id:
        raise ValueError(f"OpenAlex has no institution for ROR {org.id}")

    # Canonical institution name from OpenAlex (the graph backbone); ROR supplies the id.
    org = dataclasses.replace(org, name=institution.get("display_name") or org.name)
    seed = {**seed, "openalex_institution_id": institution_id}

    raw_authors = openalex.discover_authors(
        institution_id,
        from_year=current_year - 4,
        max_author_pages=max_author_pages,
        max_work_pages=max_work_pages,
    )
    authors = tuple(parse_openalex_author(a) for a in raw_authors)

    rows = build_import_rows(org, authors, seed, RUN_KEY, SOURCE_KEYS)

    # Query-scoped relevance over researcher nodes (cosine is 0 when embeddings are absent →
    # relevance falls back to recency + volume, which still ranks meaningfully).
    researcher_nodes = [n for n in rows["nodes"] if n["kind"] == "researcher"]
    node_meta = _node_meta(researcher_nodes)
    if embeddings is not None and researcher_nodes:
        texts = {n["id"]: _researcher_text(n) for n in researcher_nodes}
        vectors = embeddings.embed_texts(list(texts.values()))
        node_vectors = dict(zip(texts.keys(), vectors))
        seed_text = f"{seed['institution']} {seed.get('topic') or ''}".strip()
        seed_embedding = embeddings.embed_texts([seed_text])[0]
        rows["embeddings"] = [
            {"node_id": nid, "model": embeddings.model, "embedding": vec}
            for nid, vec in node_vectors.items() if vec
        ]
    else:
        node_vectors = {n["id"]: [] for n in researcher_nodes}
        seed_embedding = []
    rows["relevance"] = score_relevance(
        seed_embedding, node_vectors, node_meta, RUN_KEY, current_year
    )

    counts = load_import_rows(session, rows)

    run = session.scalar(select(RepopulationRun).where(RepopulationRun.seed == seed))
    if run is not None:
        run.status = "succeeded"
        session.commit()

    return {
        "run_id": run.id if run else None,
        "institution_id": institution_id,
        "institution_name": org.name,
        "authors_discovered": len(authors),
        "counts": counts,
        "relevance_rows": len(rows["relevance"]),
        "embeddings": embeddings is not None,
    }
