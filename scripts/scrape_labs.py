"""Phase-3 single batch command: institution -> researchers (repopulate) -> labs (scrape) -> one run.

Boots the no-Docker Postgres, loads the legacy graph (auto-published; default view unchanged), runs a
Phase-2 repopulation to get the researcher set + institution homepage, then discovers/fetches/extracts/
reconciles the lab layer into the SAME run. Additive: the run stays unpublished unless --publish.

  scripts/scrape_labs.py --institution "University of Washington" [--allow-domain washington.edu]
                         [--max-pages 40] [--no-embeddings] [--publish]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pgserver
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from sqlalchemy import select  # noqa: E402

from backend.repopulation.clients.budget import DailyBudget  # noqa: E402
from backend.repopulation.clients.embeddings import EmbeddingsClient  # noqa: E402
from backend.repopulation.clients.http import HttpClient  # noqa: E402
from backend.repopulation.clients.llm import LlmClient  # noqa: E402
from backend.repopulation.clients.openalex import OpenAlexClient  # noqa: E402
from backend.repopulation.clients.rawstore import LocalRawStore  # noqa: E402
from backend.repopulation.clients.ror import RorClient  # noqa: E402
from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import graph_from_db, load_import_rows, publish_run  # noqa: E402
from backend.repopulation.models.membership import RunNode  # noqa: E402
from backend.repopulation.models.nodes import Node, RepopulationRun  # noqa: E402
from backend.repopulation.run import run_repopulation  # noqa: E402
from backend.repopulation.scrape_run import run_lab_scrape  # noqa: E402
from backend.repopulation.scraping.fetch import Fetcher  # noqa: E402
from backend.repopulation.scraping.robots import RobotsCache  # noqa: E402

CACHE = ROOT / "public" / "graph_cache.json"
CONTACT = os.getenv("CROSSREF_MAILTO") or "sakshamsinghbhs@gmail.com"
USER_AGENT = f"PaperPigeon/0.3 (mailto:{CONTACT})"


def registrable_domain(host: str) -> str:
    parts = (host or "").lower().rstrip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--institution", required=True)
    ap.add_argument("--topic", default=None)
    ap.add_argument("--allow-domain", action="append", default=[])
    ap.add_argument("--seed-url", action="append", default=[], help="extra discovery seed URL(s)")
    ap.add_argument("--max-pages", type=int, default=40)
    ap.add_argument("--max-author-pages", type=int, default=1)
    ap.add_argument("--max-work-pages", type=int, default=3)
    ap.add_argument("--year", type=int, default=datetime.now().year)
    ap.add_argument("--no-embeddings", action="store_true")
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
    embeddings = None if args.no_embeddings else EmbeddingsClient(
        http, os.getenv("OPENROUTER_API_KEY"), budget=budget)
    llm = LlmClient(http, os.getenv("OPENROUTER_API_KEY"), budget=budget)
    robots = RobotsCache(http, USER_AGENT)

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
        print(f"      run #{run_id}: {repop['authors_discovered']} researchers")

        # Institution homepage (OpenAlex) + the run's researcher set + the run's (augmented) seed.
        org = ror.resolve(args.institution)
        inst = openalex.get_institution_by_ror(org.id)
        homepage = inst.get("homepage_url")
        institution = {"id": inst["id"], "ror": org.id, "name": inst.get("display_name")}
        domains = set(args.allow_domain)
        if not domains and homepage:
            domains.add(registrable_domain(urlparse(homepage).hostname))
        for seed_url in args.seed_url:
            host = urlparse(seed_url).hostname
            if host:
                domains.add(registrable_domain(host))
        discovery_home = homepage or (args.seed_url[0] if args.seed_url else None)

        with Session() as s:
            repop_seed = s.get(RepopulationRun, run_id).seed
            researcher_rows = s.scalars(
                select(Node).join(RunNode, RunNode.node_id == Node.id)
                .where(RunNode.run_id == run_id, Node.kind == "researcher")
            ).all()
            researcher_set = [
                {"id": n.id, "name": n.name, "normalized_name": n.normalized_name,
                 "openalex_id": n.openalex_id} for n in researcher_rows
            ]

        if not discovery_home or not domains:
            print("      no institution homepage/domain available — skipping lab scrape.")
            return 0
        print(f"\n[2/2] scraping labs from {discovery_home} (domains={sorted(domains)}) ...")
        fetcher = Fetcher(http, robots, domains)
        with Session() as s:
            scrape = run_lab_scrape(
                s, repop_seed=repop_seed, run_key="run", institution=institution,
                researcher_set=researcher_set, homepage_url=discovery_home, allowed_domains=domains,
                fetcher=fetcher, llm=llm, max_pages=args.max_pages,
                extra_seeds=tuple(args.seed_url),
            )

        print("\n=== scrape summary ===")
        for k, v in scrape.items():
            print(f"  {k}: {v}")
        print(f"  budget spent today: ${budget.spent:.4f} / ${budget.cap}")

        if args.publish:
            with Session() as s:
                publish_run(s, run_id)
            print(f"  published run #{run_id} as the default graph.")

        with Session() as s:
            default_graph = graph_from_db(s)
            run_graph = graph_from_db(s, run_id=run_id)
        print(f"\n  default graph: {len(default_graph['nodes'])} nodes / {len(default_graph['links'])} links"
              f" ({'PUBLISHED run' if args.publish else 'UNCHANGED legacy'})")
        print(f"  run #{run_id} snapshot: {len(run_graph['nodes'])} nodes / {len(run_graph['links'])} links")
        return 0
    finally:
        srv.cleanup()
        http.close()


if __name__ == "__main__":
    raise SystemExit(main())
