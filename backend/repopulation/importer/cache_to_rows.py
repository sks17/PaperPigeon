"""Transform the existing static graph cache into repop row-dicts  [Cursor task P1-T03].

Implement `cache_to_rows` per SCHEMA.md sections 1, 2, and 3. It maps the current
graph_cache.json into the legacy run's nodes/edges/source_record/relevance rows so the
serializer (P1-T02) can reproduce the original graph. Order-preserving and idempotent at the
data level (identical input -> identical output; node 'id' and (src_id,dst_id,type) are the
identity keys).

Pure function ONLY: it receives the already-parsed graph dict; it does NOT read files, hit a
DB, or call the network (the main thread loads the file and writes the rows to Postgres).
Forbidden: editing the serializer, models, the SQL migration, or anything outside this file.
"""
from __future__ import annotations

# Frontend link.type -> rich edge.type (forward map; see SCHEMA.md section 3).
RENDER_TO_RICH_EDGE_TYPE = {
    "paper": "COAUTHORED_WITH",
    "advisor": "ADVISED_BY",
    "researcher_lab": "MEMBER_OF",
}

LEGACY_RUN_KEY = "legacy"
LEGACY_SOURCE_KEY = "legacy"


def _legacy_run_row() -> dict:
    return {
        "key": LEGACY_RUN_KEY,
        "seed": {"source": "legacy_cache"},
        "status": "succeeded",
    }


def _legacy_source_record_row() -> dict:
    return {
        "key": LEGACY_SOURCE_KEY,
        "source": "legacy_cache",
        "source_url": None,
        "retrieved_at": None,
        "confidence": None,
        "evidence": "imported from public/graph_cache.json",
        "run_key": LEGACY_RUN_KEY,
        "raw_s3_key": None,
    }


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def _paper_row(paper: dict) -> dict:
    return {
        "title": paper.get("title"),
        "year": paper.get("year"),
        "document_id": paper.get("document_id"),
        "tags": _as_list(paper.get("tags")),
    }


def _researcher_attributes(node: dict) -> dict:
    return {
        "advisor": node.get("advisor"),
        "contact_info": _as_list(node.get("contact_info")),
        "labs": _as_list(node.get("labs")),
        "standing": node.get("standing"),
        "papers": [_paper_row(paper) for paper in _as_list(node.get("papers"))],
        "tags": _as_list(node.get("tags")),
        # Legacy display value, kept VERBATIM (the cache stores influence as a string, e.g. "15").
        # The serializer reproduces this exactly; the numeric engine score lives in relevance rows.
        "influence": node.get("influence"),
    }


def _node_row(node: dict) -> dict:
    kind = node.get("type")
    # Preserve `about` VERBATIM so the round-trip is exact: the cache distinguishes "" (empty
    # description present) from null (no description), and both must be reproduced faithfully.
    ai_description = node.get("about")

    return {
        "id": node["id"],
        "kind": kind,
        "name": node["name"],
        "val": node["val"],
        "orcid": None,
        "openalex_id": None,
        "ror": None,
        "normalized_name": None,
        "attributes": _researcher_attributes(node) if kind == "researcher" else {},
        "ai_description": ai_description,
        "description_model": "legacy_dynamodb" if ai_description else None,
        "description_generated_at": None,
        "description_evidence": None,
        "confidence": None,
        "source_record_key": LEGACY_SOURCE_KEY,
    }


def _edge_row(link: dict) -> dict:
    return {
        "src_id": link["source"],
        "dst_id": link["target"],
        "type": RENDER_TO_RICH_EDGE_TYPE[link["type"]],
        "weight": 1.0,
        "directed": True,
        "attributes": {},
        "confidence": None,
        "source_record_key": LEGACY_SOURCE_KEY,
    }


def _relevance_row(node: dict) -> dict | None:
    if node.get("type") != "researcher":
        return None

    influence = node.get("influence")
    if influence is None or influence == "":
        return None

    try:
        score = float(influence)
    except (TypeError, ValueError):
        # Non-numeric legacy influence is preserved verbatim on the node (for rendering) but
        # cannot seed a numeric relevance score — skip the relevance row rather than crash.
        return None

    return {
        "node_id": node["id"],
        "run_key": LEGACY_RUN_KEY,
        "score": score,
        "components": None,
    }


def cache_to_rows(graph: dict) -> dict:
    """graph = parsed graph_cache.json: {"nodes": [...], "links": [...]}.
    Returns the ImportRows dict defined in SCHEMA.md section 1.
    """
    nodes = graph.get("nodes", [])
    relevance = [_relevance_row(node) for node in nodes]

    return {
        "runs": [_legacy_run_row()],
        "source_records": [_legacy_source_record_row()],
        "nodes": [_node_row(node) for node in nodes],
        "edges": [_edge_row(link) for link in graph.get("links", [])],
        "relevance": [row for row in relevance if row is not None],
    }
