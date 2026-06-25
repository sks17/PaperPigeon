"""Gather grounded evidence for a node's RAG description (Phase 4) — main-thread (DB + pgvector).

This is the "retrieval" half of RAG: it assembles the numbered evidence list the prompt shows the
model and `build_rows` verifies against. Evidence comes from the node's OWN stored facts plus, for
researchers with embeddings, the pgvector nearest-neighbours as "related work" context:

  - researcher: affiliation, topics, papers, co-authors, pgvector-related researchers;
  - lab: the scraped self-description, PI, research areas, member researchers (MEMBER_OF).

Everything is scoped to one run (`run_id`) so an unpublished run's descriptions are built only from
that run's snapshot — preserving snapshot isolation. Evidence ids are assigned in a deterministic
order so prompts and citations are stable.

NOT pure (reads Postgres + pgvector); kept thin so the pure transforms stay testable without a DB.
"""
from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.repopulation.models.edges import Edge
from backend.repopulation.models.membership import RunNode
from backend.repopulation.models.nodes import Embedding, Node

# Caps keep the prompt (and its budget-charged token cost) bounded on prolific nodes.
MAX_PAPERS = 8
MAX_COAUTHORS = 8
MAX_MEMBERS = 12
DEFAULT_NEIGHBOURS = 5


def _accumulator() -> tuple[list[dict], "callable"]:
    """A growing evidence list + an `add(kind, text)` that assigns the next 1-based id (skips blanks)."""
    items: list[dict] = []

    def add(kind: str, text: str) -> None:
        text = (text or "").strip()
        if text:
            items.append({"id": len(items) + 1, "kind": kind, "text": text})

    return items, add


def gather_evidence(
    session: Session,
    node: Node,
    run_id: int,
    *,
    k: int = DEFAULT_NEIGHBOURS,
    model: str | None = None,
) -> list[dict]:
    """Return ordered evidence items ``{"id", "kind", "text"}`` grounding `node` within `run_id`.

    Dispatches on `node.kind`. `model` selects which embedding row to use for the researcher pgvector
    step; when None (or the node has no embedding) that step is skipped and evidence falls back to the
    node's own stored facts. Labs ground on their scraped attributes + members (no pgvector).
    """
    if node.kind == "lab":
        return _lab_evidence(session, node, run_id)
    return _researcher_evidence(session, node, run_id, k=k, model=model)


def _researcher_evidence(
    session: Session, node: Node, run_id: int, *, k: int, model: str | None
) -> list[dict]:
    attrs = node.attributes or {}
    items, _add = _accumulator()

    # ── affiliation (AFFILIATED_WITH -> institution name) ─────────────────────
    for name in _neighbour_names(session, node.id, run_id, "AFFILIATED_WITH", as_source=True):
        _add("affiliation", f"Affiliated with {name}.")

    # ── research topics (from stored tags) ────────────────────────────────────
    tags = [t for t in (attrs.get("tags") or []) if t]
    if tags:
        _add("topics", "Research topics: " + ", ".join(tags) + ".")

    # ── recent papers ─────────────────────────────────────────────────────────
    for paper in (attrs.get("papers") or [])[:MAX_PAPERS]:
        title = (paper or {}).get("title")
        if not title:
            continue
        year = paper.get("year")
        _add("paper", f'Authored "{title}"' + (f" ({year})." if year else "."))

    # ── co-authors (COAUTHORED_WITH, either direction) ────────────────────────
    coauthors = _neighbour_names(session, node.id, run_id, "COAUTHORED_WITH", both_dirs=True)
    for name in coauthors[:MAX_COAUTHORS]:
        _add("coauthor", f"Has co-authored with {name}.")

    # ── pgvector related researchers (RAG retrieval; skipped when no embedding) ─
    for name in _related_names(session, node.id, run_id, k=k, model=model):
        _add("related", f"Works in a similar area to {name}.")

    return items


def _lab_evidence(session: Session, node: Node, run_id: int) -> list[dict]:
    """Evidence for a lab node: its scraped self-description (the strongest grounding), PI, research
    areas, and member researchers (MEMBER_OF, run-scoped). No pgvector — labs aren't embedded."""
    attrs = node.attributes or {}
    items, _add = _accumulator()

    _add("description", attrs.get("description"))
    pi = attrs.get("pi")
    if pi:
        _add("pi", f"Principal investigator: {pi}.")
    areas = [a for a in (attrs.get("research_areas") or []) if a]
    if areas:
        _add("areas", "Research areas: " + ", ".join(areas) + ".")

    # Members are researchers with MEMBER_OF -> this lab (lab is the edge destination).
    for name in _neighbour_names(session, node.id, run_id, "MEMBER_OF")[:MAX_MEMBERS]:
        _add("member", f"Lab member: {name}.")

    return items


def _neighbour_names(
    session: Session,
    node_id: str,
    run_id: int,
    edge_type: str,
    *,
    both_dirs: bool = False,
    as_source: bool = False,
) -> list[str]:
    """Names of run-member nodes linked to `node_id` by `edge_type` within `run_id`, in stable name
    order. `as_source=True`: node_id is the edge source, return dst names. `both_dirs=True`:
    undirected — return the OTHER endpoint regardless of direction (symmetric COAUTHORED_WITH)."""
    if both_dirs:
        edge_rows = session.execute(
            select(Edge.src_id, Edge.dst_id).where(
                Edge.type == edge_type,
                or_(Edge.src_id == node_id, Edge.dst_id == node_id),
            )
        ).all()
        other_ids = {dst if src == node_id else src for src, dst in edge_rows}
    elif as_source:
        other_ids = set(
            session.scalars(
                select(Edge.dst_id).where(Edge.type == edge_type, Edge.src_id == node_id)
            ).all()
        )
    else:
        other_ids = set(
            session.scalars(
                select(Edge.src_id).where(Edge.type == edge_type, Edge.dst_id == node_id)
            ).all()
        )
    other_ids.discard(node_id)
    if not other_ids:
        return []

    rows = session.execute(
        select(Node.name)
        .join(RunNode, RunNode.node_id == Node.id)
        .where(Node.id.in_(other_ids), RunNode.run_id == run_id)
    ).all()
    return sorted({name for (name,) in rows})


def _related_names(
    session: Session, node_id: str, run_id: int, *, k: int, model: str | None
) -> list[str]:
    """pgvector nearest-neighbour researcher names within the run (excluding self). Empty when the
    node has no embedding for `model` (e.g. an embeddings-off run)."""
    if model is None:
        return []
    vec = session.scalar(
        select(Embedding.embedding).where(
            Embedding.node_id == node_id, Embedding.model == model
        )
    )
    if vec is None:
        return []

    rows = session.execute(
        select(Node.name)
        .join(Embedding, Embedding.node_id == Node.id)
        .join(RunNode, RunNode.node_id == Node.id)
        .where(
            Embedding.model == model,
            RunNode.run_id == run_id,
            Node.id != node_id,
            Node.kind == "researcher",
        )
        .order_by(Embedding.embedding.cosine_distance(vec))
        .limit(k)
    ).all()
    return [name for (name,) in rows]
