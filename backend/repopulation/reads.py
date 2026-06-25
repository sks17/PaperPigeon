"""Node detail reads (Phase 4) — surface grounded descriptions the graph endpoint can't carry.

`serialize_graph` renders a lab as only 4 fields and a researcher's `about` only inside a run
snapshot, so the enriched, grounded data (a lab's description/faculty/areas, any node's cited
evidence) needs its own read path. These helpers back the `/api/lab` and `/api/node/description`
endpoints; they read a node by id and are run-agnostic (a node carries its latest description).

Read-only; no writes, no graph mutation — purely additive surface over the existing data.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.repopulation.loader import get_published_run_id
from backend.repopulation.models.membership import RunEdge, RunNode
from backend.repopulation.models.nodes import Node, RepopulationRun


def list_runs(session: Session) -> list[dict]:
    """All repopulation runs with their seed, status, snapshot counts, and whether each is the
    published (default-served) run. Backs GET /api/runs so the UI can offer run snapshots — the
    place a user reaches grounded descriptions that aren't on the published graph yet."""
    published = get_published_run_id(session)
    runs = session.scalars(select(RepopulationRun).order_by(RepopulationRun.id)).all()
    out: list[dict] = []
    for run in runs:
        nodes = session.scalar(
            select(func.count()).select_from(RunNode).where(RunNode.run_id == run.id)
        )
        edges = session.scalar(
            select(func.count()).select_from(RunEdge).where(RunEdge.run_id == run.id)
        )
        out.append({
            "id": run.id,
            "seed": run.seed or {},
            "status": run.status,
            "published": run.id == published,
            "nodes": nodes or 0,
            "edges": edges or 0,
        })
    return out


def node_description(session: Session, node_id: str) -> dict | None:
    """A node's grounded description + the evidence that grounds it (None if the node is absent)."""
    node = session.get(Node, node_id)
    if node is None:
        return None
    return {
        "id": node.id,
        "name": node.name,
        "kind": node.kind,
        "about": node.ai_description,
        "description_model": node.description_model,
        "description_generated_at": (
            node.description_generated_at.isoformat() if node.description_generated_at else None
        ),
        "evidence": node.description_evidence or [],
        "confidence": node.confidence,
    }


def lab_detail(session: Session, lab_id: str) -> dict | None:
    """A lab's enriched record: grounded description, research areas, PI, url, and faculty (resolved
    to {id, name}). Returns None when the id is absent or is not a lab node."""
    node = session.get(Node, lab_id)
    if node is None or node.kind != "lab":
        return None

    attrs = node.attributes or {}
    faculty_ids = [fid for fid in (attrs.get("faculty") or []) if fid]
    faculty: list[dict] = []
    if faculty_ids:
        names = dict(
            session.execute(select(Node.id, Node.name).where(Node.id.in_(faculty_ids))).all()
        )
        faculty = [{"id": fid, "name": names.get(fid, fid)} for fid in faculty_ids]

    return {
        "id": node.id,
        "name": node.name,
        # The grounded ai_description wins; fall back to the raw scraped blurb if not yet described.
        "description": node.ai_description or attrs.get("description"),
        "description_model": node.description_model,
        "description_evidence": node.description_evidence or [],
        "research_areas": attrs.get("research_areas") or [],
        "pi": attrs.get("pi"),
        "url": attrs.get("url"),
        "faculty": faculty,
    }
