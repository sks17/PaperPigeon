"""Phase-1 local DB proof (no Docker required).

Boots a local PostgreSQL 16 + pgvector via `pgserver` (Docker was unavailable on this box),
applies migrations/0001_initial.sql, loads the existing graph_cache.json into Postgres, serves
it back through the DB-backed path, and verifies it reproduces the existing graph (same nodes,
same link multiset). Re-runs the load to prove idempotency.

Run:  .venv/Scripts/python.exe scripts/phase1_local_db.py
The PG data dir persists at .pg/ (gitignored); the server runs only for the script's lifetime.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.repopulation.db import make_engine, make_session_factory  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import graph_from_db, load_import_rows  # noqa: E402

MIGRATION = ROOT / "backend" / "repopulation" / "migrations" / "0001_initial.sql"
CACHE = ROOT / "public" / "graph_cache.json"


def _link_multiset(graph: dict) -> Counter:
    return Counter((l["source"], l["target"], l["type"]) for l in graph["links"])


def _nodes_by_id(graph: dict) -> dict:
    return {n["id"]: n for n in graph["nodes"]}


def main() -> int:
    pgdata = ROOT / ".pg"
    pgdata.mkdir(exist_ok=True)
    print(f"[1/5] booting local Postgres+pgvector at {pgdata} ...")
    srv = pgserver.get_server(pgdata)
    try:
        print(f"      up: {srv.get_uri().split('@')[-1]}")
        print("[2/5] applying migration 0001_initial.sql ...")
        srv.psql(MIGRATION.read_text(encoding="utf-8"))

        engine = make_engine(srv.get_uri())
        Session = make_session_factory(engine)

        cache = json.loads(CACHE.read_text(encoding="utf-8"))
        rows = cache_to_rows(cache)
        print("[3/5] loading legacy graph into Postgres ...")
        with Session() as s:
            counts = load_import_rows(s, rows)
        print(f"      loaded: {counts}")

        print("[4/5] serving graph back from Postgres + verifying reproduction ...")
        with Session() as s:
            db_graph = graph_from_db(s)
        nodes_ok = _nodes_by_id(db_graph) == _nodes_by_id(cache)
        links_ok = _link_multiset(db_graph) == _link_multiset(cache)
        print(f"      nodes match (by id, full content): {nodes_ok}")
        print(f"      link multiset matches (1043 incl. parallel coauthorship): {links_ok}")
        print(f"      db link count: {len(db_graph['links'])}  cache link count: {len(cache['links'])}")

        print("[5/5] idempotency: re-loading the same rows ...")
        with Session() as s:
            counts2 = load_import_rows(s, rows)
        idem_ok = counts2 == counts
        print(f"      counts unchanged after re-load: {idem_ok}  ({counts2})")

        ok = nodes_ok and links_ok and idem_ok
        print("\nRESULT:", "PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        srv.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
