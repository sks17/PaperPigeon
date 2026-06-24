"""Local repopulation CLI (no Docker, no AWS).

Boots the no-Docker Postgres, applies migrations, loads the legacy graph (auto-published, so the
existing graph stays the default view), then submits a seed through the in-process queue and runs
the worker — exercising the full live pipeline (ROR + OpenAlex). Embeddings are off by default
until the provider is confirmed (relevance falls back to recency+volume).

  .venv/Scripts/python.exe scripts/repopulate.py --institution "University of Washington" --topic "computer vision"
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pgserver
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import os  # noqa: E402

from backend.repopulation.clients.budget import DailyBudget  # noqa: E402
from backend.repopulation.clients.embeddings import EmbeddingsClient  # noqa: E402
from backend.repopulation.clients.http import HttpClient  # noqa: E402
from backend.repopulation.clients.openalex import OpenAlexClient  # noqa: E402
from backend.repopulation.clients.rawstore import LocalRawStore  # noqa: E402
from backend.repopulation.clients.ror import RorClient  # noqa: E402
from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import graph_from_db, load_import_rows  # noqa: E402
from backend.repopulation.queue import InProcessQueue  # noqa: E402
from backend.repopulation.run import run_repopulation  # noqa: E402

CACHE = ROOT / "public" / "graph_cache.json"
CONTACT = os.getenv("CROSSREF_MAILTO") or "sakshamsinghbhs@gmail.com"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--institution", required=True)
    ap.add_argument("--topic", default=None)
    ap.add_argument("--max-author-pages", type=int, default=2)
    ap.add_argument("--max-work-pages", type=int, default=5)
    ap.add_argument("--year", type=int, default=datetime.now().year)
    ap.add_argument("--no-embeddings", action="store_true", help="skip OpenRouter embeddings")
    args = ap.parse_args()

    if not os.getenv("OPENALEX_API_KEY"):
        print("ERROR: OPENALEX_API_KEY not set in .env (mandatory since 2026-02-13).")
        return 2

    store = LocalRawStore(ROOT / ".raw_cache")
    http = HttpClient(store, {"api.ror.org", "api.openalex.org", "openrouter.ai"},
                      f"PaperPigeon/0.2 (mailto:{CONTACT})")
    cap = os.getenv("PAPERPIGEON_BUDGET_PRO_DAILY_USD")
    budget = DailyBudget(
        float(cap) if cap else None, ROOT / ".budget_ledger.json",
        datetime.now().date().isoformat(),
    )
    print(f"daily budget: cap=${budget.cap} spent_today=${budget.spent:.4f}")

    ror = RorClient(http)
    openalex = OpenAlexClient(http, api_key=os.getenv("OPENALEX_API_KEY"), budget=budget)

    embeddings = None
    if not args.no_embeddings and os.getenv("OPENROUTER_API_KEY"):
        embeddings = EmbeddingsClient(http, os.getenv("OPENROUTER_API_KEY"), budget=budget)

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

        queue = InProcessQueue()
        queue.submit({"institution": args.institution, "topic": args.topic, "keywords": []})

        job = queue.poll()
        print(f"\nrepopulating: institution={args.institution!r} topic={args.topic!r} "
              f"(caps: authors={args.max_author_pages}p, works={args.max_work_pages}p)")
        with Session() as s:
            summary = run_repopulation(
                s, job.seed, ror=ror, openalex=openalex, current_year=args.year,
                embeddings=embeddings, max_author_pages=args.max_author_pages,
                max_work_pages=args.max_work_pages,
            )

        est_cost = http.live_calls * 0.10 / 1000  # list+filter ≈ $0.10 / 1k requests
        print("\n=== run summary ===")
        for k, v in summary.items():
            print(f"  {k}: {v}")
        print(f"  openalex/ror live_calls: {http.live_calls}  cache_hits: {http.cache_hits}")
        print(f"  est. OpenAlex cost: ${est_cost:.4f}")
        print(f"  budget spent today: ${budget.spent:.4f} / ${budget.cap}  remaining: ${budget.remaining():.4f}")

        with Session() as s:
            default_graph = graph_from_db(s)
            run_graph = graph_from_db(s, run_id=summary["run_id"])
        print("\n=== serving ===")
        print(f"  default (published legacy): {len(default_graph['nodes'])} nodes / "
              f"{len(default_graph['links'])} links  (UNCHANGED)")
        print(f"  run #{summary['run_id']} snapshot: {len(run_graph['nodes'])} nodes / "
              f"{len(run_graph['links'])} links  (view via GET /api/graph/data?run={summary['run_id']})")
        return 0
    finally:
        srv.cleanup()
        http.close()


if __name__ == "__main__":
    raise SystemExit(main())
