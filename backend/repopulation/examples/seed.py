"""Seed the committed example run snapshots into Postgres (idempotent; runs on deploy).

Each example JSON (see this package's __init__) is a run snapshot in the shape:

    {
      "institution": "...",
      "import_rows": { runs, source_records, nodes, edges, relevance },  # loader.load_import_rows
      "description_updates": [ {node_id, ai_description, description_model,
                               description_generated_at, description_evidence}, ... ]
    }

`load_import_rows` creates the run + nodes/edges/relevance/provenance + run-membership and is
idempotent (ON CONFLICT DO NOTHING). `apply_description_updates` then writes the grounded
descriptions + evidence onto the nodes (the node upsert is DO-NOTHING, so descriptions need the
explicit UPDATE; it is also idempotent). Examples never auto-publish, so the default served graph
(the legacy UW graph) is unchanged — they appear only in the run-snapshot picker.

build_example.py is the (dev-only) generator; this module is product code on the deploy path.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.repopulation.loader import apply_description_updates, load_import_rows
from backend.repopulation.models.membership import RunNode
from backend.repopulation.models.nodes import RepopulationRun

EXAMPLES_DIR = Path(__file__).resolve().parent


def example_files() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.json"))


def _already_seeded(session: Session, seed: dict) -> bool:
    """True when a run with this exact seed already has snapshot members (skip the re-load)."""
    run = session.scalar(select(RepopulationRun).where(RepopulationRun.seed == seed))
    if run is None:
        return False
    members = session.scalar(
        select(func.count()).select_from(RunNode).where(RunNode.run_id == run.id)
    )
    return bool(members)


def seed_example_runs(session: Session) -> dict:
    """Load every committed example snapshot (idempotent). Returns {name: status}."""
    results: dict[str, str] = {}
    for path in example_files():
        artifact = json.loads(path.read_text(encoding="utf-8"))
        rows = artifact["import_rows"]
        seed = rows["runs"][0]["seed"]
        if _already_seeded(session, seed):
            results[path.stem] = "already-seeded"
            continue
        load_import_rows(session, rows)
        apply_description_updates(session, artifact.get("description_updates", []))
        results[path.stem] = f"seeded ({len(rows['nodes'])} nodes, {len(rows['edges'])} edges)"
    return results
