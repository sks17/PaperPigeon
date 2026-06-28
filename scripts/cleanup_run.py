"""Delete a repopulation run and the data that belonged ONLY to it (guarded, dry-run by default).

Drops a run's snapshot from Postgres — its run-membership, plus any node / edge / source_record
used by no other run — without touching the published graph or anything another run still shares.
Use it to remove a stale run (e.g. an old discovery superseded by a committed example) so the
run-snapshot picker shows only current data.

Safety:
  * DRY-RUN by default: prints exactly what would be deleted; pass --yes to actually delete.
  * Refuses to delete the PUBLISHED run (the default served graph).
  * Shared rows (nodes/edges/sources used by another run) are preserved — only run-exclusive data
    is removed. Everything runs in one transaction.

Target the run by id or by seed institution:
  python scripts/cleanup_run.py --run-id 2                 # preview
  python scripts/cleanup_run.py --run-id 2 --yes           # delete
  python scripts/cleanup_run.py --institution "University of Toronto"   # preview (errors if >1 match)

Reads DATABASE_URL from the environment (or .env). For the deployed DB, set it to the prod
connection string first (see the deploy docs), or run inside the container via `fly ssh console`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # python-dotenv optional in the deployed image
    pass

from sqlalchemy import delete, func, select  # noqa: E402

from backend.repopulation.db import make_engine, make_session_factory  # noqa: E402
from backend.repopulation.loader import get_published_run_id  # noqa: E402
from backend.repopulation.models.edges import Edge  # noqa: E402
from backend.repopulation.models.membership import RunEdge, RunNode  # noqa: E402
from backend.repopulation.models.nodes import Node, RepopulationRun  # noqa: E402
from backend.repopulation.models.provenance import SourceRecord  # noqa: E402


def _resolve_run(session, run_id: int | None, institution: str | None) -> RepopulationRun | None:
    if run_id is not None:
        return session.get(RepopulationRun, run_id)
    matches = session.scalars(
        select(RepopulationRun).where(
            RepopulationRun.seed["institution"].astext == institution
        )
    ).all()
    if not matches:
        return None
    if len(matches) > 1:
        ids = ", ".join(f"#{r.id} (seed={r.seed})" for r in matches)
        raise SystemExit(
            f"{len(matches)} runs match institution {institution!r}: {ids}\n"
            f"Re-run with --run-id to pick one."
        )
    return matches[0]


def _count(session, stmt) -> int:
    return session.scalar(select(func.count()).select_from(stmt.subquery())) or 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Delete a repopulation run and its exclusive data.")
    target = ap.add_mutually_exclusive_group(required=True)
    target.add_argument("--run-id", type=int, help="run id to delete")
    target.add_argument("--institution", type=str, help="seed institution to match (must be unique)")
    ap.add_argument("--yes", action="store_true", help="actually delete (default: dry-run preview)")
    args = ap.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set (put it in .env or set $env:DATABASE_URL).", file=sys.stderr)
        return 2

    factory = make_session_factory(make_engine(os.environ["DATABASE_URL"]))
    with factory() as session:
        run = _resolve_run(session, args.run_id, args.institution)
        if run is None:
            print("No matching run found.")
            return 1

        published = get_published_run_id(session)
        if run.id == published:
            print(f"Refusing to delete run #{run.id}: it is the PUBLISHED run (the default graph).")
            return 3

        # Membership of THIS run vs every OTHER run.
        this_nodes = select(RunNode.node_id).where(RunNode.run_id == run.id)
        other_nodes = select(RunNode.node_id).where(RunNode.run_id != run.id)
        this_edges = select(RunEdge.edge_id).where(RunEdge.run_id == run.id)
        other_edges = select(RunEdge.edge_id).where(RunEdge.run_id != run.id)

        # Exclusive = belongs to this run and to no other run → safe to delete.
        exclusive_nodes = select(Node.id).where(Node.id.in_(this_nodes), Node.id.not_in(other_nodes))
        exclusive_edges = select(Edge.id).where(Edge.id.in_(this_edges), Edge.id.not_in(other_edges))

        total_nodes = _count(session, this_nodes)
        total_edges = _count(session, this_edges)
        del_nodes = _count(session, exclusive_nodes)
        del_edges = _count(session, exclusive_edges)
        src_rows = _count(session, select(SourceRecord.id).where(SourceRecord.run_id == run.id))

        print(f"Run #{run.id}  status={run.status}  seed={run.seed}")
        print(f"  members:   {total_nodes} nodes, {total_edges} edges")
        print(f"  to delete: {del_nodes} run-exclusive nodes, {del_edges} run-exclusive edges "
              f"(+ their relevance/embeddings/membership), up to {src_rows} source records")
        print(f"  preserved: {total_nodes - del_nodes} shared nodes, {total_edges - del_edges} "
              f"shared edges; published run #{published} untouched")

        if not args.yes:
            print("\nDry run - nothing deleted. Re-run with --yes to apply.")
            return 0

        # Deleting a node cascades to its edges (src/dst), embeddings, relevance, and run_node;
        # deleting an edge cascades to run_edge. Order: exclusive nodes, then exclusive edges whose
        # endpoints were shared (so survived), then now-orphaned source records, then the run.
        session.execute(delete(Node).where(Node.id.in_(this_nodes), Node.id.not_in(other_nodes)))
        session.execute(delete(Edge).where(Edge.id.in_(this_edges), Edge.id.not_in(other_edges)))
        # Remove this run's source records only if nothing surviving still points at them.
        referenced_by_node = select(Node.source_record_id).where(Node.source_record_id.isnot(None))
        referenced_by_edge = select(Edge.source_record_id).where(Edge.source_record_id.isnot(None))
        session.execute(
            delete(SourceRecord).where(
                SourceRecord.run_id == run.id,
                SourceRecord.id.not_in(referenced_by_node),
                SourceRecord.id.not_in(referenced_by_edge),
            )
        )
        # The run row last — cascades any remaining run_node/run_edge/relevance/quarantine.
        session.execute(delete(RepopulationRun).where(RepopulationRun.id == run.id))
        session.commit()

        print(f"\nDeleted run #{run.id}: {del_nodes} nodes, {del_edges} edges removed. "
              f"Published run #{published} unchanged.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
