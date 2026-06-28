"""Discovery worker — claims queued discovery_jobs and runs the ingestion pipeline (main-thread).

Always-on fly process group (min 1 machine). Each tick claims one queued job with
`FOR UPDATE SKIP LOCKED` (race-safe; lets you scale workers later), then runs the SAME pipeline the
CLIs use — `run_repopulation` → `describe_run` (+ optional `run_lab_scrape` → describe labs) — against
the real DATABASE_URL, with a DB-backed daily budget. EVERY job is wrapped so any failure records the
error on the job AND flips a stuck `running` run to `failed` (fixes the documented stuck-`running`
bug). A startup reaper requeues jobs orphaned by a killed worker. SIGTERM drains gracefully.

Run:  DATABASE_URL=… OPENALEX_API_KEY=… OPENROUTER_API_KEY=… python -m backend.repopulation.worker
"""
from __future__ import annotations

import os
import signal
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select, update
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except Exception:  # python-dotenv is optional in the deployed image
    pass

from backend.repopulation.clients.budget import DbDailyBudget  # noqa: E402
from backend.repopulation.clients.embeddings import EmbeddingsClient  # noqa: E402
from backend.repopulation.clients.http import HttpClient  # noqa: E402
from backend.repopulation.clients.llm import LlmClient  # noqa: E402
from backend.repopulation.clients.openalex import OpenAlexClient  # noqa: E402
from backend.repopulation.clients.rawstore import LocalRawStore  # noqa: E402
from backend.repopulation.clients.ror import RorClient  # noqa: E402
from backend.repopulation.db import make_engine, make_session_factory  # noqa: E402
from backend.repopulation.describe_run import describe_run  # noqa: E402
from backend.repopulation.models.discovery_job import (  # noqa: E402
    JOB_FAILED,
    JOB_QUEUED,
    JOB_RUNNING,
    JOB_SUCCEEDED,
    DiscoveryJob,
)
from backend.repopulation.models.membership import RunNode  # noqa: E402
from backend.repopulation.models.nodes import Node, RepopulationRun  # noqa: E402
from backend.repopulation.run import run_repopulation  # noqa: E402
from backend.repopulation.scrape_run import run_lab_scrape  # noqa: E402
from backend.repopulation.scraping.fetch import Fetcher  # noqa: E402
from backend.repopulation.scraping.robots import RobotsCache  # noqa: E402

API_HOSTS = {"api.ror.org", "api.openalex.org", "openrouter.ai"}
CONTACT = os.getenv("CROSSREF_MAILTO") or "sakshamsinghbhs@gmail.com"
USER_AGENT = f"PaperPigeon/0.5 (mailto:{CONTACT})"
WORKER_ID = f"{os.getenv('FLY_MACHINE_ID') or uuid.uuid4().hex[:8]}"

# Per-job cost caps (env-overridable) — bound OpenAlex paging + describe + scrape work per discovery.
MAX_AUTHOR_PAGES = int(os.getenv("DISCOVERY_MAX_AUTHOR_PAGES", "1"))
MAX_WORK_PAGES = int(os.getenv("DISCOVERY_MAX_WORK_PAGES", "2"))
DESCRIBE_LIMIT = int(os.getenv("DISCOVERY_DESCRIBE_LIMIT", "80"))
MAX_SCRAPE_PAGES = int(os.getenv("DISCOVERY_MAX_SCRAPE_PAGES", "25"))
POLL_INTERVAL = float(os.getenv("DISCOVERY_POLL_INTERVAL", "3.0"))
REAP_AFTER_SECONDS = int(os.getenv("DISCOVERY_REAP_AFTER_SECONDS", "1800"))
MAX_ATTEMPTS = int(os.getenv("DISCOVERY_MAX_ATTEMPTS", "3"))
RAW_CACHE_DIR = os.getenv("RAW_CACHE_DIR") or str(Path(tempfile.gettempdir()) / "pp_raw_cache")

_stop = False


def _now():
    return datetime.now(timezone.utc)


def _registrable_domain(host: str | None) -> str | None:
    parts = (host or "").lower().rstrip(".").split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or None)


def _build_clients(session_factory):
    """Wire the live clients (one HttpClient serves both the JSON APIs and arbitrary-domain scraping;
    get_text bypasses the fixed allowlist — see http.py).

    Both upstream keys are OPTIONAL so the engine degrades gracefully instead of crashing a job:
      - OpenAlex without a key uses the keyless/polite pool (lower rate limit, identified by the
        mailto User-Agent) — enough for on-demand single-institution discovery.
      - OpenRouter absent disables embeddings + grounded descriptions; the core graph (researchers,
        co-authorship, estimated labs) is still built. Production sets both keys, so its behavior is
        unchanged; this only changes what happens when a key is missing."""
    openalex_key = os.environ.get("OPENALEX_API_KEY") or None
    openrouter_key = os.environ.get("OPENROUTER_API_KEY") or None
    if not openalex_key:
        print("worker: OPENALEX_API_KEY not set — using OpenAlex keyless polite pool", flush=True)
    if not openrouter_key:
        print("worker: OPENROUTER_API_KEY not set — skipping embeddings + descriptions", flush=True)

    cap_env = os.getenv("PAPERPIGEON_BUDGET_PRO_DAILY_USD")
    budget = DbDailyBudget(session_factory, float(cap_env) if cap_env else None, _now().date())
    store = LocalRawStore(RAW_CACHE_DIR)
    http = HttpClient(store, API_HOSTS, USER_AGENT)
    return {
        "http": http,
        "budget": budget,
        "ror": RorClient(http),
        "openalex": OpenAlexClient(http, api_key=openalex_key, budget=budget),
        "embeddings": EmbeddingsClient(http, openrouter_key, budget=budget) if openrouter_key else None,
        "llm": LlmClient(http, openrouter_key, budget=budget) if openrouter_key else None,
        "robots": RobotsCache(http, USER_AGENT),
    }


def claim_job(session: Session) -> int | None:
    """Claim the oldest queued job (FOR UPDATE SKIP LOCKED) and flip it to running. Returns its id."""
    job = session.scalars(
        select(DiscoveryJob)
        .where(DiscoveryJob.status == JOB_QUEUED)
        .order_by(DiscoveryJob.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()
    if job is None:
        return None
    job.status = JOB_RUNNING
    job.stage = "discovering"
    job.worker_id = WORKER_ID
    job.started_at = _now()
    session.commit()
    return job.id


def _set(session: Session, job_id: int, **fields) -> None:
    session.execute(update(DiscoveryJob).where(DiscoveryJob.id == job_id).values(**fields))
    session.commit()


def process_job(session_factory, job_id: int) -> None:
    """Run the full pipeline for one claimed job. Raises on failure (caller records it)."""
    clients = _build_clients(session_factory)
    current_year = _now().year
    generated_at = _now().isoformat()

    with session_factory() as session:
        job = session.get(DiscoveryJob, job_id)
        seed = dict(job.seed)
        scrape = bool(job.scrape)

        # ── discover researchers ──────────────────────────────────────────────
        repop = run_repopulation(
            session, seed, ror=clients["ror"], openalex=clients["openalex"],
            current_year=current_year, embeddings=clients["embeddings"],
            max_author_pages=MAX_AUTHOR_PAGES, max_work_pages=MAX_WORK_PAGES,
        )
        run_id = repop["run_id"]

        # ── grounded researcher descriptions (only when an LLM provider is configured) ──
        if clients["llm"] is not None:
            _set(session, job_id, run_id=run_id, stage="describing")
            describe_run(
                session, run_id, llm=clients["llm"], generated_at=generated_at,
                model=clients["llm"].model,
                embedding_model=clients["embeddings"].model if clients["embeddings"] else None,
                kinds=("researcher",), limit=DESCRIBE_LIMIT,
            )
        else:
            _set(session, job_id, run_id=run_id, stage="describing")

        # ── optional lab scraping + lab descriptions (needs the LLM extractor) ──
        if scrape and clients["llm"] is not None:
            _set(session, job_id, stage="scraping")
            _scrape_labs(session, clients, seed, run_id, generated_at)

        _set(session, job_id, status=JOB_SUCCEEDED, stage="done", finished_at=_now())


def _scrape_labs(session, clients, seed, run_id, generated_at) -> None:
    org = clients["ror"].resolve(seed["institution"])
    if org is None:
        return
    inst = clients["openalex"].get_institution_by_ror(org.id)
    homepage = inst.get("homepage_url")
    domain = _registrable_domain(urlparse(homepage).hostname) if homepage else None
    if not homepage or not domain:
        return  # no site to scrape — researchers-only run, not an error

    institution = {"id": inst["id"], "ror": org.id, "name": inst.get("display_name")}
    repop_seed = session.get(RepopulationRun, run_id).seed
    researcher_rows = session.scalars(
        select(Node)
        .join(RunNode, RunNode.node_id == Node.id)
        .where(RunNode.run_id == run_id, Node.kind == "researcher")
    ).all()
    researcher_set = [
        {"id": n.id, "name": n.name, "normalized_name": n.normalized_name, "openalex_id": n.openalex_id}
        for n in researcher_rows
    ]
    fetcher = Fetcher(clients["http"], clients["robots"], {domain})
    run_lab_scrape(
        session, repop_seed=repop_seed, run_key="run", institution=institution,
        researcher_set=researcher_set, homepage_url=homepage, allowed_domains={domain},
        fetcher=fetcher, llm=clients["llm"], max_pages=MAX_SCRAPE_PAGES,
    )
    describe_run(
        session, run_id, llm=clients["llm"], generated_at=generated_at,
        model=clients["llm"].model,
        embedding_model=clients["embeddings"].model if clients["embeddings"] else None,
        kinds=("researcher", "lab"), limit=DESCRIBE_LIMIT,
    )


def handle_failure(session_factory, job_id: int, exc: Exception) -> None:
    """Record the failure on the job and clear any run left stuck in 'running' for this institution
    (the stuck-'running'-forever fix, now that discovery routes through the worker)."""
    message = f"{type(exc).__name__}: {exc}"[:2000]
    with session_factory() as session:
        job = session.get(DiscoveryJob, job_id)
        if job is not None:
            job.status = JOB_FAILED
            job.error = message
            job.finished_at = _now()
            institution = (job.seed or {}).get("institution")
            if institution:
                session.execute(
                    update(RepopulationRun)
                    .where(
                        RepopulationRun.status == "running",
                        RepopulationRun.seed["institution"].astext == institution,
                    )
                    .values(status="failed")
                )
        session.commit()


def seed_examples(session_factory) -> None:
    """Idempotently seed the committed example run snapshots (e.g. University of Toronto) on boot.

    Done HERE rather than in the deploy's release command because load_import_rows does many per-row
    round-trips; on a remote managed Postgres that load overruns fly's release-command timeout and
    aborts the deploy. The worker has no such timeout, so it seeds in the background. Idempotent
    (skips runs already present) and fault-tolerant — a seed failure must never stop job draining."""
    try:
        from backend.repopulation.examples.seed import seed_example_runs

        with session_factory() as session:
            status = seed_example_runs(session)
        print(f"worker {WORKER_ID}: example runs: {status or 'none'}", flush=True)
    except Exception as exc:  # noqa: BLE001 — bootstrap data is non-critical; never block the worker
        print(f"worker {WORKER_ID}: example seed skipped ({type(exc).__name__}: {exc})", flush=True)


def reap_orphans(session_factory) -> None:
    """On boot, recover jobs left 'running' by a killed worker: requeue (attempts+1), dead-letter
    after MAX_ATTEMPTS."""
    now = _now()
    cutoff = now - timedelta(seconds=REAP_AFTER_SECONDS)
    with session_factory() as session:
        rows = session.scalars(
            select(DiscoveryJob).where(
                DiscoveryJob.status == JOB_RUNNING,
                DiscoveryJob.started_at < cutoff,
            )
        ).all()
        for job in rows:
            if job.attempts + 1 >= MAX_ATTEMPTS:
                job.status = JOB_FAILED
                job.error = "exceeded max attempts (worker restart)"
                job.finished_at = cutoff
            else:
                job.status = JOB_QUEUED
                job.stage = JOB_QUEUED
                job.attempts += 1
                job.worker_id = None
                job.started_at = None
        session.commit()


def _install_signal_handlers() -> None:
    def _handle(signum, _frame):
        global _stop
        _stop = True
        print(f"worker {WORKER_ID}: received signal {signum}, draining...", flush=True)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):
            pass  # not in the main thread (e.g. under a test harness)


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set.", file=sys.stderr)
        return 2
    factory = make_session_factory(make_engine())
    _install_signal_handlers()
    reap_orphans(factory)
    seed_examples(factory)
    print(f"discovery worker {WORKER_ID} started (poll={POLL_INTERVAL}s)", flush=True)

    while not _stop:
        with factory() as session:
            job_id = claim_job(session)
        if job_id is None:
            time.sleep(POLL_INTERVAL)
            continue
        print(f"worker {WORKER_ID}: processing job {job_id}", flush=True)
        try:
            process_job(factory, job_id)
            print(f"worker {WORKER_ID}: job {job_id} succeeded", flush=True)
        except Exception as exc:  # noqa: BLE001 — any failure becomes a recorded failed job
            handle_failure(factory, job_id, exc)
            print(f"worker {WORKER_ID}: job {job_id} FAILED: {exc}", flush=True)
    print(f"worker {WORKER_ID} stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
