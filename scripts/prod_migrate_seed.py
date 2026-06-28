"""Production DB bootstrap: apply migrations + seed the legacy graph (idempotent).

Run as the fly.io `release_command` (see fly.toml) on every deploy, against the managed Postgres in
`DATABASE_URL`. Safe to re-run: the migrations are all `IF NOT EXISTS` (additive-only invariant), and
the legacy graph is loaded only when the node table is empty (load_import_rows is itself idempotent,
but the emptiness guard avoids re-reading 323 nodes on every deploy).

  DATABASE_URL=postgresql://… python scripts/prod_migrate_seed.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402
from sqlalchemy import func, select  # noqa: E402

from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.examples.seed import seed_example_runs  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import load_import_rows  # noqa: E402
from backend.repopulation.models.nodes import Node  # noqa: E402

CACHE = ROOT / "public" / "graph_cache.json"


def _libpq_url(url: str) -> str:
    """psycopg wants a bare libpq URL (no SQLAlchemy +psycopg driver suffix)."""
    return url.replace("postgresql+psycopg://", "postgresql://")


def main() -> int:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 2

    # Apply each migration's full SQL text; the files carry their own BEGIN/COMMIT, so run in
    # autocommit and let the script control the transaction (mirrors `psql -f`).
    with psycopg.connect(_libpq_url(url), autocommit=True) as conn:
        for migration in migration_files():
            print(f"applying {migration.name} ...")
            conn.execute(migration.read_text(encoding="utf-8"))

    factory = make_session_factory(make_engine(url))
    with factory() as session:
        count = session.scalar(select(func.count()).select_from(Node))
        if count and count > 0:
            print(f"graph already seeded ({count} nodes) — skipping legacy load.")
        else:
            counts = load_import_rows(
                session, cache_to_rows(json.loads(CACHE.read_text(encoding="utf-8")))
            )
            print(f"seeded legacy graph: {counts}")

        # Committed example run snapshots (e.g. University of Toronto), idempotent + never published —
        # they appear only in the run-snapshot picker, leaving the default served graph unchanged.
        example_status = seed_example_runs(session)
        print(f"example runs: {example_status or 'none'}")

    print("prod migrate + seed complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
