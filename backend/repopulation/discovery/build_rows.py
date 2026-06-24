"""Build ImportRows from parsed OpenAlex/ROR dataclasses  [Cursor task P2-T01].

Implement `build_import_rows` per DISCOVERY.md: map a resolved institution + discovered authors
(+ their recent works/topics) into the ImportRows shape (SCHEMA.md §1), with the node `val`
convention, dedup keys, typed/weighted/provenance-bearing edges, and one source_record per source.

PURE: no HTTP, no DB, no network, no wall-clock. It receives already-parsed dataclasses
(OpenAlexAuthor / RorOrganization) — the main thread owns all live API integration. `relevance` is
returned empty here; it is computed post-embedding by relevance/score.py.

Forbidden: importing the clients/* modules, requests/httpx/urllib, touching the loader/serializer.
"""
from __future__ import annotations

import re
from itertools import combinations
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # type hints only — no runtime dependency, keeps this transform decoupled.
    from backend.repopulation.sources.openalex_parse import OpenAlexAuthor, OpenAlexWork
    from backend.repopulation.sources.ror_parse import RorOrganization

# Node `val` convention (DISCOVERY.md): extends frontend 1=researcher / 2=lab.
NODE_VAL = {
    "researcher": 1, "lab": 2, "institution": 3,
    "topic": 4, "paper": 5, "department": 6, "venue": 7,
}

# Rich edge types emitted by Phase 2 discovery (subset of the migration's CHECK list).
AFFILIATED_WITH = "AFFILIATED_WITH"
AUTHORED = "AUTHORED"
WORKS_ON = "WORKS_ON"
COAUTHORED_WITH = "COAUTHORED_WITH"

# API-sourced facts carry full confidence (DISCOVERY.md). RAG-grounded scoring is Phase 4.
API_CONFIDENCE = 1.0


def build_import_rows(
    institution: "RorOrganization",
    authors: "tuple[OpenAlexAuthor, ...]",
    seed: dict,
    run_key: str,
    source_keys: dict,
) -> dict:
    """institution: RorOrganization; authors: tuple[OpenAlexAuthor, ...]. Returns ImportRows
    (SCHEMA.md §1) with relevance=[] (computed separately). See DISCOVERY.md for the full mapping.

    Deterministic + idempotent at the data level: identical input -> identical output; node `id`
    and edge `(src_id, dst_id, type)` are stable identity keys.
    """
    openalex_key = source_keys["openalex"]
    ror_key = source_keys["ror"]

    institution_id = seed.get("openalex_institution_id") or institution.id

    nodes: list[dict] = []
    edges: list[dict] = []
    seen_node_ids: set[str] = set()
    seen_edge_keys: set[tuple[str, str, str]] = set()

    def _add_node(row: dict) -> None:
        if row["id"] in seen_node_ids:
            return
        seen_node_ids.add(row["id"])
        nodes.append(row)

    def _add_edge(src_id: str, dst_id: str, type_: str, weight: float, source_record_key: str) -> None:
        key = (src_id, dst_id, type_)
        if key in seen_edge_keys:
            return
        seen_edge_keys.add(key)
        edges.append(_edge_row(src_id, dst_id, type_, weight, source_record_key))

    # ── institution node (resolved via ROR) ──────────────────────────────────
    _add_node(
        _node_row(
            id=institution_id,
            kind="institution",
            name=institution.name,
            ror=institution.id,
            openalex_id=institution_id,
            normalized_name=_normalize(institution.name),
            attributes={"country": institution.country},
            source_record_key=ror_key,
        )
    )

    # ── researcher / topic / paper nodes + their edges (all from OpenAlex) ────
    for author in authors:
        _add_node(
            _node_row(
                id=author.id,
                kind="researcher",
                name=author.display_name,
                orcid=author.orcid,
                openalex_id=author.id,
                normalized_name=_normalize(author.display_name),
                attributes={
                    "papers": [_paper_attr(work) for work in author.recent_works],
                    "tags": [t.display_name for t in author.topics],
                    "h_index": author.h_index,
                    "works_count": author.works_count,
                },
                source_record_key=openalex_key,
            )
        )

        if _affiliated_with_seed(author, institution_id, institution.id):
            _add_edge(author.id, institution_id, AFFILIATED_WITH, 1.0, openalex_key)

        for work in author.recent_works:
            _add_node(
                _node_row(
                    id=work.id,
                    kind="paper",
                    name=work.title,
                    openalex_id=work.id,
                    attributes={
                        "year": work.publication_year,
                        "cited_by_count": work.cited_by_count,
                        "doi": work.doi,
                    },
                    source_record_key=openalex_key,
                )
            )
            _add_edge(author.id, work.id, AUTHORED, 1.0, openalex_key)

        for topic in author.topics:
            _add_node(
                _node_row(
                    id=topic.id,
                    kind="topic",
                    name=topic.display_name,
                    openalex_id=topic.id,
                    attributes={
                        "field": topic.field,
                        "subfield": topic.subfield,
                        "domain": topic.domain,
                    },
                    source_record_key=openalex_key,
                )
            )
            _add_edge(author.id, topic.id, WORKS_ON, _topic_share(topic.score), openalex_key)

    # ── co-authorship edges: pairs sharing a work within the discovered set ───
    for (src_id, dst_id), joint_works in sorted(_coauthor_weights(authors).items()):
        _add_edge(src_id, dst_id, COAUTHORED_WITH, float(joint_works), openalex_key)

    return {
        "runs": [{"key": run_key, "seed": seed, "status": "running"}],
        "source_records": [
            _source_record_row(openalex_key, "openalex", run_key,
                               "OpenAlex authors/works for the seed institution"),
            _source_record_row(ror_key, "ror", run_key,
                               "ROR organization record for the seed institution"),
        ],
        "nodes": nodes,
        "edges": edges,
        "relevance": [],
    }


def _node_row(
    *,
    id: str,
    kind: str,
    name: str,
    attributes: dict,
    source_record_key: str,
    orcid: str | None = None,
    openalex_id: str | None = None,
    ror: str | None = None,
    normalized_name: str | None = None,
) -> dict:
    return {
        "id": id,
        "kind": kind,
        "name": name,
        "val": NODE_VAL[kind],
        "orcid": orcid,
        "openalex_id": openalex_id,
        "ror": ror,
        "normalized_name": normalized_name,
        "attributes": attributes,
        "ai_description": None,
        "description_model": None,
        "description_generated_at": None,
        "description_evidence": None,
        "confidence": API_CONFIDENCE,
        "source_record_key": source_record_key,
    }


def _edge_row(src_id: str, dst_id: str, type_: str, weight: float, source_record_key: str) -> dict:
    return {
        "src_id": src_id,
        "dst_id": dst_id,
        "type": type_,
        "weight": float(weight),
        "directed": True,
        "attributes": {},
        "confidence": API_CONFIDENCE,
        "source_record_key": source_record_key,
    }


def _source_record_row(key: str, source: str, run_key: str, evidence: str) -> dict:
    return {
        "key": key,
        "source": source,
        "source_url": None,
        "retrieved_at": None,
        "confidence": None,
        "evidence": evidence,
        "run_key": run_key,
        "raw_s3_key": None,
    }


def _paper_attr(work: "OpenAlexWork") -> dict:
    return {
        "title": work.title,
        "year": work.publication_year,
        "document_id": work.id,
        "tags": [topic.display_name for topic in work.topics],
    }


def _affiliated_with_seed(
    author: "OpenAlexAuthor", institution_id: str, institution_ror: str | None
) -> bool:
    """True when the author's last-known institution is the resolved seed institution."""
    inst = author.last_known_institution
    if inst is None:
        return False
    if inst.id is not None and inst.id == institution_id:
        return True
    return bool(inst.ror and institution_ror and inst.ror == institution_ror)


def _coauthor_weights(authors: "tuple[OpenAlexAuthor, ...]") -> dict[tuple[str, str], int]:
    """Map each (src_id, dst_id) author pair to the number of works they jointly authored within
    the discovered set. Direction is fixed by ascending author id (symmetric-norm deferred)."""
    work_authors: dict[str, list[str]] = {}
    for author in authors:
        for work in author.recent_works:
            bucket = work_authors.setdefault(work.id, [])
            if author.id not in bucket:
                bucket.append(author.id)

    weights: dict[tuple[str, str], int] = {}
    for author_ids in work_authors.values():
        for src_id, dst_id in combinations(sorted(author_ids), 2):
            weights[(src_id, dst_id)] = weights.get((src_id, dst_id), 0) + 1
    return weights


def _topic_share(score: Any) -> float:
    """WORKS_ON weight is the topic share (OpenAlex topic.score); default 0.0 when absent."""
    return float(score) if score is not None else 0.0


def _normalize(name: str | None) -> str | None:
    """Lowercased, whitespace-collapsed name for the normalized_name dedup key."""
    if name is None:
        return None
    collapsed = re.sub(r"\s+", " ", name).strip().lower()
    return collapsed or None
