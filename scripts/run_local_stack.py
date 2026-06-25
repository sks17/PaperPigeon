"""Run the Phase-1 local stack: no-Docker Postgres+pgvector -> FastAPI on :8000.

Boots pgserver (data dir .pg/), applies the migration, idempotently loads the legacy graph,
then serves backend.repopulation.api:app via uvicorn on :8000. Vite dev (port 5173) proxies
/api -> :8000 (see vite.config.ts), so the existing frontend renders off the new backend.

Run:  .venv/Scripts/python.exe scripts/run_local_stack.py [--demo]
  --demo  also seeds a small GROUNDED repopulation run (no network), so the run-snapshot picker
          and the grounded-description provenance UI are demonstrable out of the box.
Prints 'STACK_READY ...' once the API is up. Ctrl-C to stop (pgserver is cleaned up).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import load_import_rows  # noqa: E402

CACHE = ROOT / "public" / "graph_cache.json"
PORT = int(os.environ.get("PORT", "8000"))


def main() -> int:
    demo = "--demo" in sys.argv[1:]
    pgdata = ROOT / ".pg"
    pgdata.mkdir(exist_ok=True)
    srv = pgserver.get_server(pgdata)
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        uri = srv.get_uri()
        factory = make_session_factory(make_engine(uri))
        with factory() as session:
            counts = load_import_rows(session, cache_to_rows(json.loads(CACHE.read_text("utf-8"))))

        demo_info = None
        if demo:
            from scripts.demo_data import seed_demo_run  # local import: dev/demo only

            with factory() as session:
                demo_info = seed_demo_run(session)

        os.environ["DATABASE_URL"] = uri  # backend.repopulation.api reads this lazily

        import uvicorn

        print(f"STACK_READY api=http://127.0.0.1:{PORT} loaded={counts}", flush=True)
        if demo_info:
            print(f"DEMO_RUN {demo_info}", flush=True)
        uvicorn.run("backend.repopulation.api:app", host="127.0.0.1", port=PORT, log_level="warning")
        return 0
    finally:
        srv.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
