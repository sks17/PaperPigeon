"""One-command local dev stack: Postgres+pgvector -> FastAPI (:8000) + in-process discovery worker.

Extends scripts/run_local_stack.py with a live discovery worker so the frontend's "Discover" button
actually generates new graphs locally — no Docker, no AWS, and no upstream API keys required:

  * Loads the legacy UW graph into Postgres (auto-published → the default view), so the graph
    populates immediately.
  * Runs the discovery worker in a background thread against the SAME database, so POST /api/discover
    jobs are claimed and processed in-process.
  * OpenAlex is called keyless (polite pool); OpenRouter is optional (descriptions/embeddings are
    skipped when its key is absent — the researchers + co-authorship + estimated labs still build).
  * The discovery endpoints are gated by DISCOVERY_API_KEY, defaulted to 'abx1213' for local use —
    type that into the modal's "API key" field.

Run:  python scripts/run_local_dev.py
Then: pnpm dev   (separate terminal; Vite on :5173 proxies /api -> :8000)

Prints 'STACK_READY ...' once the API is up. Ctrl-C stops everything (pgserver is cleaned up).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.repopulation.db import make_engine, make_session_factory, migration_files  # noqa: E402
from backend.repopulation.importer.cache_to_rows import cache_to_rows  # noqa: E402
from backend.repopulation.loader import load_import_rows  # noqa: E402

CACHE = ROOT / "public" / "graph_cache.json"
PORT = int(os.environ.get("PORT", "8000"))
DEFAULT_DISCOVERY_KEY = "abx1213"


def main() -> int:
    pgdata = ROOT / ".pg"
    pgdata.mkdir(exist_ok=True)
    srv = pgserver.get_server(pgdata)
    try:
        for migration in migration_files():
            srv.psql(migration.read_text(encoding="utf-8"))
        uri = srv.get_uri()

        # Shared config for BOTH the API (this process) and the worker thread.
        os.environ["DATABASE_URL"] = uri
        os.environ.setdefault("DISCOVERY_API_KEY", DEFAULT_DISCOVERY_KEY)
        # Denser-but-still-fast keyless demo graphs (override via env if desired).
        os.environ.setdefault("DISCOVERY_MAX_AUTHOR_PAGES", "2")
        os.environ.setdefault("DISCOVERY_MAX_WORK_PAGES", "3")

        factory = make_session_factory(make_engine(uri))
        with factory() as session:
            counts = load_import_rows(session, cache_to_rows(json.loads(CACHE.read_text("utf-8"))))

        # Discovery worker in a daemon thread: claims POST /api/discover jobs and runs the live
        # pipeline against the same Postgres. Imported here so importing this module never boots it.
        from backend.repopulation import worker  # noqa: E402

        threading.Thread(target=worker.main, name="discovery-worker", daemon=True).start()

        import uvicorn

        key = os.environ["DISCOVERY_API_KEY"]
        print(f"STACK_READY api=http://127.0.0.1:{PORT} loaded={counts}", flush=True)
        print(f"DISCOVERY_KEY={key}  (enter this in the Discover modal's 'API key' field)", flush=True)
        print("Frontend: run `pnpm dev` in another terminal, then open http://localhost:5173", flush=True)
        uvicorn.run("backend.repopulation.api:app", host="127.0.0.1", port=PORT, log_level="warning")
        return 0
    finally:
        srv.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
