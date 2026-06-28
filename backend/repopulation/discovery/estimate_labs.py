"""Estimate research-group ("lab") affiliations from co-authorship structure.

The only first-class lab signal in the pipeline is web-scraped lab pages (build_lab_rows.py),
reconciled by exact name match. For a novel university that path frequently yields nothing — no
discoverable homepage, lab-page URLs that miss the crawler's patterns, or member names that don't
match OpenAlex exactly — so the graph ends up with NO lab affiliations at all.

This module fills that gap with an *estimate* derived from data we already have: the co-authorship
graph of the discovered cohort. Researchers who repeatedly publish together form a community; the
most senior member (highest h-index / output) is taken as the group's anchor (PI). Each community
becomes an estimated `lab` node with `MEMBER_OF` edges, explicitly flagged `estimated: true` and
carrying a sub-1.0 confidence so a later real scrape always outranks it.

PURE: no HTTP, no DB, no clock, no randomness. Deterministic + idempotent — identical input yields
identical output, lab ids are stable (derived from the anchor's OpenAlex id), and the community
detection iterates nodes in a fixed order with id-based tie-breaks.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # type hints only — keep this transform decoupled, like build_rows.
    from backend.repopulation.sources.openalex_parse import OpenAlexAuthor

MEMBER_OF = "MEMBER_OF"
LAB_VAL = 2  # frontend node convention (researcher=1, lab=2)
# Provenance source: 'ai' is the closed-vocabulary value (source_record CHECK / models.provenance)
# for a machine-derived record. The specific derivation is recorded in `attributes.method` below.
ESTIMATE_SOURCE = "ai"
ESTIMATE_METHOD = "coauthorship"
ESTIMATE_DESC_MODEL = "coauthorship-estimate"

# A credible estimated lab is a handful of people, not a lone pair and not a whole department.
MIN_LAB_SIZE = 3
MAX_LAB_SIZE = 40
# Label-propagation rounds: communities stabilise quickly; the cap just bounds pathological inputs.
MAX_PROPAGATION_ROUNDS = 12


def estimate_labs(
    institution_id: str,
    authors: "tuple[OpenAlexAuthor, ...]",
    coauthor_weights: dict[tuple[str, str], int],
    run_key: str,
    source_record_key: str,
) -> dict:
    """Return {"nodes": [...], "edges": [...], "source_records": [...]} of estimated lab rows.

    `coauthor_weights` maps an ordered author-id pair to the number of works they jointly authored
    within the cohort (exactly what build_rows._coauthor_weights produces). Returns empty lists when
    no community clears MIN_LAB_SIZE, so callers can skip emitting a provenance record entirely.
    """
    adjacency = _adjacency(coauthor_weights)
    if not adjacency:
        return {"nodes": [], "edges": [], "source_records": []}

    authors_by_id = {author.id: author for author in authors}
    communities = _detect_communities(adjacency)

    nodes: list[dict] = []
    edges: list[dict] = []
    for member_ids in communities:
        if not (MIN_LAB_SIZE <= len(member_ids) <= MAX_LAB_SIZE):
            continue
        members = [authors_by_id[mid] for mid in member_ids if mid in authors_by_id]
        if len(members) < MIN_LAB_SIZE:
            continue

        anchor = _anchor(members)
        lab_id = f"lab:estimated:{institution_id}:{_short_id(anchor.id)}"
        member_id_list = sorted(author.id for author in members)
        confidence = _confidence(member_id_list, adjacency)

        nodes.append(
            _lab_node(
                lab_id=lab_id,
                name=_lab_name(anchor),
                members=member_id_list,
                pi_name=anchor.display_name,
                research_areas=_top_research_areas(members),
                confidence=confidence,
                source_record_key=source_record_key,
            )
        )
        for member_id in member_id_list:
            edges.append(
                _member_edge(member_id, lab_id, confidence, source_record_key)
            )

    if not nodes:
        return {"nodes": [], "edges": [], "source_records": []}

    return {
        "nodes": nodes,
        "edges": edges,
        "source_records": [
            {
                "key": source_record_key,
                "source": ESTIMATE_SOURCE,
                "source_url": None,
                "retrieved_at": None,
                "confidence": None,
                "evidence": "Research groups estimated from co-authorship communities.",
                "run_key": run_key,
                "raw_s3_key": None,
            }
        ],
    }


def _adjacency(coauthor_weights: dict[tuple[str, str], int]) -> dict[str, dict[str, float]]:
    """Symmetric weighted adjacency from the directed (src<dst) co-authorship weight map."""
    adjacency: dict[str, dict[str, float]] = {}
    for (src_id, dst_id), weight in coauthor_weights.items():
        if not weight:
            continue
        adjacency.setdefault(src_id, {})[dst_id] = float(weight)
        adjacency.setdefault(dst_id, {})[src_id] = float(weight)
    return adjacency


def _detect_communities(adjacency: dict[str, dict[str, float]]) -> list[list[str]]:
    """Weighted label propagation (deterministic). Each node adopts the label carrying the greatest
    summed edge weight across its neighbours; ties break to the smallest label id. Nodes are visited
    in sorted id order every round, so the result is fully reproducible."""
    labels = {node_id: node_id for node_id in adjacency}
    nodes_sorted = sorted(adjacency)

    for _ in range(MAX_PROPAGATION_ROUNDS):
        changed = False
        for node_id in nodes_sorted:
            best = _dominant_label(adjacency[node_id], labels, current=labels[node_id])
            if best != labels[node_id]:
                labels[node_id] = best
                changed = True
        if not changed:
            break

    communities: dict[str, list[str]] = {}
    for node_id in nodes_sorted:
        communities.setdefault(labels[node_id], []).append(node_id)
    return list(communities.values())


def _dominant_label(
    neighbours: dict[str, float], labels: dict[str, str], *, current: str
) -> str:
    """Label with the highest neighbour-weight sum; ties resolved toward the smallest label id, with
    the node's current label preferred on an exact tie to damp oscillation."""
    scores: dict[str, float] = {}
    for neighbour_id, weight in neighbours.items():
        label = labels[neighbour_id]
        scores[label] = scores.get(label, 0.0) + weight
    if not scores:
        return current
    best_score = max(scores.values())
    candidates = sorted(label for label, score in scores.items() if score == best_score)
    if current in candidates:
        return current
    return candidates[0]


def _anchor(members: "list[OpenAlexAuthor]") -> "OpenAlexAuthor":
    """The group's PI proxy: most senior member by h-index, then output, then a stable id tiebreak."""
    return max(
        members,
        key=lambda a: (a.h_index or 0, a.works_count or 0, _short_id(a.id)),
    )


def _confidence(member_ids: list[str], adjacency: dict[str, dict[str, float]]) -> float:
    """Scale confidence (0.4–0.7) by how densely the community is internally connected — a fully
    interconnected group reads as a real lab; a loose chain reads as a weaker guess."""
    size = len(member_ids)
    member_set = set(member_ids)
    internal_edges = sum(
        1
        for member_id in member_ids
        for neighbour_id in adjacency.get(member_id, {})
        if neighbour_id in member_set and neighbour_id > member_id
    )
    max_edges = size * (size - 1) / 2
    density = internal_edges / max_edges if max_edges else 0.0
    return round(0.4 + 0.3 * density, 3)


def _top_research_areas(members: "list[OpenAlexAuthor]", limit: int = 5) -> list[str]:
    """Most shared topics across the group, ranked by summed topic score (a proxy for what the lab
    works on). Deterministic: ties break alphabetically."""
    scores: dict[str, float] = {}
    for member in members:
        for topic in member.topics:
            name = topic.display_name
            if name:
                scores[name] = scores.get(name, 0.0) + (topic.score or 0.0)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [name for name, _ in ranked[:limit]]


def _lab_name(anchor: "OpenAlexAuthor") -> str:
    return f"{anchor.display_name} Group"


def _lab_node(
    *,
    lab_id: str,
    name: str,
    members: list[str],
    pi_name: str,
    research_areas: list[str],
    confidence: float,
    source_record_key: str,
) -> dict:
    return {
        "id": lab_id,
        "kind": "lab",
        "name": name,
        "val": LAB_VAL,
        "orcid": None,
        "openalex_id": None,
        "ror": None,
        "normalized_name": _normalize(name),
        "attributes": {
            "faculty": members,
            "pi": pi_name,
            "research_areas": research_areas,
            "estimated": True,
            "method": ESTIMATE_METHOD,
        },
        "ai_description": None,
        "description_model": ESTIMATE_DESC_MODEL,
        "description_generated_at": None,
        "description_evidence": None,
        "confidence": confidence,
        "source_record_key": source_record_key,
    }


def _member_edge(
    src_id: str, lab_id: str, confidence: float, source_record_key: str
) -> dict:
    return {
        "src_id": src_id,
        "dst_id": lab_id,
        "type": MEMBER_OF,
        "weight": 1.0,
        "directed": True,
        "attributes": {"estimated": True},
        "confidence": confidence,
        "source_record_key": source_record_key,
    }


def _short_id(entity_id: str) -> str:
    return entity_id.rstrip("/").rsplit("/", 1)[-1]


def _normalize(name: str | None) -> str | None:
    if name is None:
        return None
    collapsed = " ".join(name.split()).strip().lower()
    return collapsed or None
