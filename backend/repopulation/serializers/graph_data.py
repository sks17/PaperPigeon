"""Serialize repop row-dicts into the frontend graph contract  [Cursor task P1-T02].

Implement `serialize_graph` per SCHEMA.md sections 1, 3, and 4. It is the inverse of the
importer for the rendered subset and is order-preserving (so the round-trip golden test
P1-T05 reproduces the original graph structurally).

Pure function ONLY: no DB, no I/O, no network. Forbidden: editing the importer, models,
the SQL migration, or anything outside this file.
"""
from __future__ import annotations

# Rich edge.type -> frontend link.type (see SCHEMA.md section 3). Edges whose rich type is
# not in this map are NOT rendered in Phase 1.
RICH_TO_RENDER_LINK_TYPE = {
    "COAUTHORED_WITH": "paper",
    "ADVISED_BY": "advisor",
    "MEMBER_OF": "researcher_lab",
}


def serialize_graph(
    nodes: list[dict],
    edges: list[dict],
    relevance_by_node: dict[str, float],
) -> dict:
    """nodes/edges = node_row/edge_row lists (SCHEMA.md section 2), order-preserving.
    relevance_by_node = {node_id: score} for the active run (legacy run in Phase 1).
    Returns {"nodes": [...], "links": [...]} matching SCHEMA.md section 4.

    Researcher nodes MUST emit all 12 keys (null where absent); lab nodes exactly 4 keys.
    """
    rendered_nodes = []
    for node in nodes:
        kind = node.get("kind")
        if kind == "researcher":
            attributes = node.get("attributes") or {}
            rendered_nodes.append(
                {
                    "id": node.get("id"),
                    "name": node.get("name"),
                    "type": "researcher",
                    "val": 1,
                    "advisor": attributes.get("advisor"),
                    "contact_info": attributes.get("contact_info"),
                    "labs": attributes.get("labs"),
                    "standing": attributes.get("standing"),
                    "papers": attributes.get("papers"),
                    "tags": attributes.get("tags"),
                    # Legacy influence is reproduced VERBATIM from attributes (the cache stores it
                    # as a string); only Phase-2 nodes without it fall back to the run's score.
                    "influence": attributes.get(
                        "influence", relevance_by_node.get(node.get("id"))
                    ),
                    "about": node.get("ai_description"),
                }
            )
        elif kind == "lab":
            rendered_nodes.append(
                {
                    "id": node.get("id"),
                    "name": node.get("name"),
                    "type": "lab",
                    "val": 2,
                }
            )

    rendered_links = []
    for edge in edges:
        rendered_type = RICH_TO_RENDER_LINK_TYPE.get(edge.get("type"))
        if rendered_type is None:
            continue

        rendered_links.append(
            {
                "source": edge.get("src_id"),
                "target": edge.get("dst_id"),
                "type": rendered_type,
            }
        )

    return {"nodes": rendered_nodes, "links": rendered_links}
