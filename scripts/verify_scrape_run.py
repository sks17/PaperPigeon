"""Verify the OUTPUT of a lab-scrape run against the additive + grounded-provenance guarantees.

  scripts/verify_scrape_run.py <run_id>

Boots the persisted local Postgres (.pg) and asserts: every lab node in the run has a 'scrape'
source_record + confidence; every accepted MEMBER_OF edge points at an existing researcher; each
lab's evidence anchor is recorded; the default published graph is unchanged (323/1043) unless this
run is published; quarantined records are reported. Exits non-zero on any failed assertion.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pgserver
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.repopulation.db import make_engine, make_session_factory  # noqa: E402
from backend.repopulation.loader import get_published_run_id, graph_from_db  # noqa: E402
from backend.repopulation.models.edges import Edge  # noqa: E402
from backend.repopulation.models.membership import Quarantine, RunEdge, RunNode  # noqa: E402
from backend.repopulation.models.nodes import Node  # noqa: E402
from backend.repopulation.models.provenance import SourceRecord  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: verify_scrape_run.py <run_id>")
        return 2
    run_id = int(sys.argv[1])

    srv = pgserver.get_server(ROOT / ".pg")
    failures: list[str] = []
    try:
        Session = make_session_factory(make_engine(srv.get_uri()))
        with Session() as s:
            lab_nodes = s.scalars(
                select(Node).join(RunNode, RunNode.node_id == Node.id)
                .where(RunNode.run_id == run_id, Node.kind == "lab")
            ).all()
            for n in lab_nodes:
                sr = s.get(SourceRecord, n.source_record_id) if n.source_record_id else None
                if sr is None or sr.source != "scrape":
                    failures.append(f"lab {n.id}: missing 'scrape' provenance")
                if n.confidence is None:
                    failures.append(f"lab {n.id}: missing confidence")
                if not (n.attributes or {}).get("description") and not (sr and sr.evidence):
                    failures.append(f"lab {n.id}: no description/evidence anchor (ungrounded)")

            node_ids = set(s.scalars(select(Node.id)).all())
            member_edges = s.scalars(
                select(Edge).join(RunEdge, RunEdge.edge_id == Edge.id)
                .where(RunEdge.run_id == run_id, Edge.type == "MEMBER_OF")
            ).all()
            for e in member_edges:
                if e.src_id not in node_ids or e.dst_id not in node_ids:
                    failures.append(f"MEMBER_OF {e.src_id}->{e.dst_id}: dangling node")

            default = graph_from_db(s)
            published = get_published_run_id(s)
            if published != run_id and (len(default["nodes"]), len(default["links"])) != (323, 1043):
                failures.append(
                    f"default graph changed while run unpublished: "
                    f"{len(default['nodes'])}/{len(default['links'])} (expected 323/1043)"
                )
            quarantined = s.scalar(
                select(func.count()).select_from(Quarantine).where(Quarantine.run_id == run_id)
            )

        print(f"lab nodes: {len(lab_nodes)} | MEMBER_OF edges: {len(member_edges)} | "
              f"quarantined: {quarantined} | published_run: {published}")
        if failures:
            print("\nFAILED:")
            for f in failures:
                print("  -", f)
            return 1
        print("\nVERIFY: PASS")
        return 0
    finally:
        srv.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
