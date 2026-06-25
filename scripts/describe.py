"""Phase-4 command: institution -> researchers (repopulate, embedded) -> grounded RAG descriptions.

Boots the no-Docker Postgres, loads the legacy graph (auto-published; default view unchanged), runs a
Phase-2 repopulation WITH embeddings (so pgvector "related-researcher" evidence is available), then
generates grounded descriptions for that run's researchers and writes them onto the run's nodes.
Additive: the run stays unpublished (its `about` text is visible only via ?run=<id>) unless --publish.

  scripts/describe.py --institution "University of Washington" [--topic "computer vision"]
                      [--max-author-pages 1] [--min-confidence 0.5] [--limit 50] [--publish]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pgserver
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from backend.repopulation.clients.budget import DailyBudget  # noqa: E402
from backend.repopulation.clients.embeddings import EmbeddingsClient  # noqa: E402
from backend.repopulation.clients.http import HttpClient  # noqa: E402
from backend.repopulation.clients.llm import LlmClient  # noqa: E402
from backend.repopulation.clients.openalex import OpenAlexClient  # noqa: E402
from backend.repopulation.clients.rawstore import LocalRawStore  # noqa: E402
from backend.repopulation.clients.ror import RorClient  # noqa: E402
from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.describe_run import describe_run  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import graph_from_db, load_import_rows, publish_run  # noqa: E402
from backend.repopulation.promote import promote_descriptions  # noqa: E402
from backend.repopulation.run import run_repopulation  # noqa: E402

CACHE = ROOT / "public" / "graph_cache.json"
CONTACT = os.getenv("CROSSREF_MAILTO") or "sakshamsinghbhs@gmail.com"
USER_AGENT = f"PaperPigeon/0.4 (mailto:{CONTACT})"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--institution", required=True)
    ap.add_argument("--topic", default=None)
    ap.add_argument("--max-author-pages", type=int, default=1)
    ap.add_argument("--max-work-pages", type=int, default=3)
    ap.add_argument("--year", type=int, default=datetime.now().year)
    ap.add_argument("--min-confidence", type=float, default=0.5)
    ap.add_argument("--neighbours", type=int, default=5)
    ap.add_argument("--limit", type=int, default=None, help="cap nodes described (cost guard)")
    ap.add_argument("--no-embeddings", action="store_true", help="ground on stored facts only")
    ap.add_argument("--promote", action="store_true",
                    help="enrich the published graph's researchers with this run's grounded "
                         "descriptions (fills empty bios; preserves existing unless --overwrite)")
    ap.add_argument("--overwrite", action="store_true",
                    help="with --promote, replace existing published descriptions too")
    ap.add_argument("--publish", action="store_true")
    args = ap.parse_args()

    if not os.getenv("OPENALEX_API_KEY") or not os.getenv("OPENROUTER_API_KEY"):
        print("ERROR: OPENALEX_API_KEY and OPENROUTER_API_KEY must be set in .env")
        return 2

    store = LocalRawStore(ROOT / ".raw_cache")
    http = HttpClient(store, {"api.ror.org", "api.openalex.org", "openrouter.ai"}, USER_AGENT)
    cap = os.getenv("PAPERPIGEON_BUDGET_PRO_DAILY_USD")
    budget = DailyBudget(float(cap) if cap else None, ROOT / ".budget_ledger.json",
                         datetime.now().date().isoformat())
    print(f"daily budget: cap=${budget.cap} spent_today=${budget.spent:.4f}")

    ror = RorClient(http)
    openalex = OpenAlexClient(http, api_key=os.getenv("OPENALEX_API_KEY"), budget=budget)
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    embeddings = None if args.no_embeddings else EmbeddingsClient(http, openrouter_key, budget=budget)
    llm = LlmClient(http, openrouter_key, budget=budget)
    embedding_model = embeddings.model if embeddings is not None else None

    pgdata = ROOT / ".pg"
    pgdata.mkdir(exist_ok=True)
    srv = pgserver.get_server(pgdata)
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        Session = make_session_factory(make_engine(srv.get_uri()))
        with Session() as s:
            load_import_rows(s, cache_to_rows(json.loads(CACHE.read_text(encoding="utf-8"))))
        print("legacy graph loaded + published (default view unchanged).")

        seed = {"institution": args.institution, "topic": args.topic, "keywords": []}
        print(f"\n[1/2] repopulating researchers: {args.institution!r} ...")
        with Session() as s:
            repop = run_repopulation(s, dict(seed), ror=ror, openalex=openalex,
                                     current_year=args.year, embeddings=embeddings,
                                     max_author_pages=args.max_author_pages,
                                     max_work_pages=args.max_work_pages)
        run_id = repop["run_id"]
        print(f"      run #{run_id}: {repop['authors_discovered']} researchers "
              f"(embeddings={'on' if embeddings else 'off'})")

        print("\n[2/2] generating grounded descriptions ...")
        generated_at = datetime.now(timezone.utc).isoformat()
        with Session() as s:
            summary = describe_run(
                s, run_id, llm=llm, generated_at=generated_at, model=llm.model,
                min_confidence=args.min_confidence, neighbours=args.neighbours,
                embedding_model=embedding_model, limit=args.limit,
            )

        print("\n=== describe summary ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        print(f"  budget spent today: ${budget.spent:.4f} / ${budget.cap}")

        if args.promote:
            with Session() as s:
                promo = promote_descriptions(s, run_id, overwrite=args.overwrite)
            print("\n=== promote summary (published graph enriched) ===")
            for k, v in promo.items():
                print(f"  {k}: {v}")

        if args.publish:
            with Session() as s:
                publish_run(s, run_id)
            print(f"  published run #{run_id} as the default graph.")

        with Session() as s:
            default_graph = graph_from_db(s)
            run_graph = graph_from_db(s, run_id=run_id)
        described = sum(1 for n in run_graph["nodes"] if n.get("about"))
        print(f"\n  default graph: {len(default_graph['nodes'])} nodes "
              f"({'PUBLISHED run' if args.publish else 'UNCHANGED legacy'})")
        print(f"  run #{run_id} snapshot: {len(run_graph['nodes'])} nodes, "
              f"{described} with grounded `about` (view via GET /api/graph/data?run={run_id})")
        return 0
    finally:
        srv.cleanup()
        http.close()


if __name__ == "__main__":
    raise SystemExit(main())
