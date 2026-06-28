"""Generate a committed example run snapshot with the LIVE discovery pipeline (dev/maintenance tool).

Runs the real engine for a seed institution — ROR + OpenAlex (cohort-aligned co-authorship +
estimated labs) + grounded descriptions — into a throwaway Postgres, then exports the run as a
self-contained snapshot JSON under backend/repopulation/examples/. `examples/seed.py` loads that
snapshot on every deploy, so the deployed app ships a reproducible, new-approach example.

  python scripts/build_example.py --institution "University of Toronto"
  # optional: --topic "machine learning" --out <path> --describe-limit 80 \
  #           --max-author-pages 1 --max-work-pages 3 --no-describe

Requires OPENALEX_API_KEY + OPENROUTER_API_KEY in .env (grounded descriptions + embeddings).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pgserver
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import os  # noqa: E402

from sqlalchemy import select  # noqa: E402

from backend.repopulation.clients.budget import DailyBudget  # noqa: E402
from backend.repopulation.clients.embeddings import EmbeddingsClient  # noqa: E402
from backend.repopulation.clients.http import HttpClient  # noqa: E402
from backend.repopulation.clients.llm import LlmClient  # noqa: E402
from backend.repopulation.clients.openalex import OpenAlexClient  # noqa: E402
from backend.repopulation.clients.rawstore import LocalRawStore  # noqa: E402
from backend.repopulation.clients.ror import RorClient  # noqa: E402
from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.describe_run import describe_run  # noqa: E402
from backend.repopulation.models.edges import Edge  # noqa: E402
from backend.repopulation.models.membership import RunEdge, RunNode  # noqa: E402
from backend.repopulation.models.nodes import Node, Relevance, RepopulationRun  # noqa: E402
from backend.repopulation.models.provenance import SourceRecord  # noqa: E402
from backend.repopulation.run import run_repopulation  # noqa: E402

EXAMPLES_DIR = ROOT / "backend" / "repopulation" / "examples"
CONTACT = os.getenv("CROSSREF_MAILTO") or "sakshamsinghbhs@gmail.com"
RUN_KEY = "example"


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def export_run(session, run_id: int) -> dict:
    """Serialize a run's snapshot into the import-rows + description-updates artifact shape."""
    run = session.get(RepopulationRun, run_id)
    nodes = session.scalars(
        select(Node).join(RunNode, RunNode.node_id == Node.id).where(RunNode.run_id == run_id)
    ).all()
    edges = session.scalars(
        select(Edge).join(RunEdge, RunEdge.edge_id == Edge.id).where(RunEdge.run_id == run_id)
    ).all()
    relevance = session.scalars(select(Relevance).where(Relevance.run_id == run_id)).all()

    # Resolve the source_records the nodes/edges point at, keyed by source name (unique per run here).
    src_ids = {n.source_record_id for n in nodes} | {e.source_record_id for e in edges}
    src_ids.discard(None)
    srcs = (
        session.scalars(select(SourceRecord).where(SourceRecord.id.in_(src_ids))).all()
        if src_ids else []
    )
    id_to_key: dict[int, str] = {}
    source_records = []
    for s in srcs:
        key = s.source if s.source not in {r["key"] for r in source_records} else f"{s.source}:{s.id}"
        id_to_key[s.id] = key
        source_records.append({
            "key": key, "source": s.source, "source_url": s.source_url,
            "retrieved_at": _iso(s.retrieved_at), "confidence": s.confidence,
            "evidence": s.evidence, "run_key": RUN_KEY, "raw_s3_key": s.raw_s3_key,
        })
    fallback_key = source_records[0]["key"] if source_records else None

    def _key(source_record_id):
        return id_to_key.get(source_record_id, fallback_key)

    node_rows = [{
        "id": n.id, "kind": n.kind, "name": n.name, "val": n.val, "orcid": n.orcid,
        "openalex_id": n.openalex_id, "ror": n.ror, "normalized_name": n.normalized_name,
        "attributes": n.attributes or {}, "ai_description": n.ai_description,
        "description_model": n.description_model, "confidence": n.confidence,
        "source_record_key": _key(n.source_record_id),
    } for n in nodes]
    edge_rows = [{
        "src_id": e.src_id, "dst_id": e.dst_id, "type": e.type, "weight": e.weight,
        "directed": e.directed, "attributes": e.attributes or {}, "confidence": e.confidence,
        "source_record_key": _key(e.source_record_id),
    } for e in edges]
    relevance_rows = [{
        "node_id": r.node_id, "run_key": RUN_KEY, "score": r.score, "components": r.components,
    } for r in relevance]
    description_updates = [{
        "node_id": n.id, "ai_description": n.ai_description,
        "description_model": n.description_model,
        "description_generated_at": _iso(n.description_generated_at),
        "description_evidence": n.description_evidence,
    } for n in nodes if n.ai_description]

    # Self-identifying seed so the shipped example is its own canonical run — it never collides
    # with an ad-hoc discovery of the same institution (so seeding is collision-free + idempotent).
    example_seed = {**run.seed, "example": True}

    return {
        "institution": run.seed.get("institution"),
        "generated_with": "cohort co-authorship + estimated labs (new discovery approach)",
        "import_rows": {
            "runs": [{"key": RUN_KEY, "seed": example_seed, "status": "succeeded"}],
            "source_records": source_records,
            "nodes": node_rows,
            "edges": edge_rows,
            "relevance": relevance_rows,
        },
        "description_updates": description_updates,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--institution", required=True)
    ap.add_argument("--topic", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-author-pages", type=int, default=1)
    ap.add_argument("--max-work-pages", type=int, default=3)
    ap.add_argument("--describe-limit", type=int, default=80)
    ap.add_argument("--no-describe", action="store_true")
    ap.add_argument("--year", type=int, default=datetime.now().year)
    args = ap.parse_args()

    if not os.getenv("OPENALEX_API_KEY"):
        print("ERROR: OPENALEX_API_KEY not set in .env", file=sys.stderr)
        return 2

    store = LocalRawStore(ROOT / ".raw_cache")
    http = HttpClient(store, {"api.ror.org", "api.openalex.org", "openrouter.ai"},
                      f"PaperPigeon/0.5 (mailto:{CONTACT})")
    cap = os.getenv("PAPERPIGEON_BUDGET_PRO_DAILY_USD")
    budget = DailyBudget(float(cap) if cap else None, ROOT / ".budget_ledger.json",
                         datetime.now().date().isoformat())
    ror = RorClient(http)
    openalex = OpenAlexClient(http, api_key=os.getenv("OPENALEX_API_KEY"), budget=budget)
    openrouter = os.getenv("OPENROUTER_API_KEY")
    embeddings = EmbeddingsClient(http, openrouter, budget=budget) if openrouter else None
    llm = LlmClient(http, openrouter, budget=budget) if openrouter else None

    pgdata = ROOT / ".pg_example"
    pgdata.mkdir(exist_ok=True)
    srv = pgserver.get_server(pgdata)
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        Session = make_session_factory(make_engine(srv.get_uri()))

        seed = {"institution": args.institution, "topic": args.topic, "keywords": []}
        with Session() as s:
            summary = run_repopulation(
                s, seed, ror=ror, openalex=openalex, current_year=args.year,
                embeddings=embeddings, max_author_pages=args.max_author_pages,
                max_work_pages=args.max_work_pages,
            )
        run_id = summary["run_id"]
        print(f"discovered: {summary['authors_discovered']} authors -> run #{run_id}")

        if not args.no_describe and llm is not None:
            with Session() as s:
                desc = describe_run(
                    s, run_id, llm=llm, generated_at=datetime.now().isoformat(),
                    model=llm.model, embedding_model=embeddings.model if embeddings else None,
                    kinds=("researcher", "lab"), limit=args.describe_limit,
                )
            print(f"described: {desc['described']} nodes ({desc['quarantined']} quarantined)")

        with Session() as s:
            artifact = export_run(s, run_id)

        out = Path(args.out) if args.out else EXAMPLES_DIR / f"{_slug(args.institution)}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(artifact, indent=2, ensure_ascii=False), encoding="utf-8")
        rows = artifact["import_rows"]
        print(f"wrote {out}")
        print(f"  nodes={len(rows['nodes'])} edges={len(rows['edges'])} "
              f"descriptions={len(artifact['description_updates'])}")
        print(f"  budget spent today: ${budget.spent:.4f} / ${budget.cap}")
        return 0
    finally:
        srv.cleanup()
        http.close()


if __name__ == "__main__":
    raise SystemExit(main())
