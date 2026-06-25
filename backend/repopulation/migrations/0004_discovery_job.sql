-- Paper Pigeon — Repopulation Engine — Migration 0004 (discovery jobs + budget ledger)
--
-- ADDITIVE-ONLY (enforced by tests/test_migration_additive.py across all migrations). Backs the
-- async, key-gated on-demand discovery service: a POST enqueues a discovery_job; an always-on
-- worker claims it (FOR UPDATE SKIP LOCKED), runs the repopulation+describe(+scrape) pipeline, and
-- records status/errors. budget_ledger moves daily spend into Postgres (fly's filesystem is
-- ephemeral + per-machine, so the file-based ledger is unsafe across worker restarts/machines).
--
-- Run by the MAIN THREAD only:  psql "$DATABASE_URL" -f backend/repopulation/migrations/0004_discovery_job.sql

BEGIN;

CREATE TABLE IF NOT EXISTS repop.discovery_job (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seed          jsonb NOT NULL,                 -- {institution, topic, keywords}
    seed_hash     text  NOT NULL,                 -- sha256 of normalized institution+topic (scrape excluded)
    scrape        boolean NOT NULL DEFAULT false, -- opt-in lab scraping stage
    status        text  NOT NULL DEFAULT 'queued',
    stage         text  NOT NULL DEFAULT 'queued',-- queued|discovering|describing|scraping|done
    run_id        bigint REFERENCES repop.repopulation_run(id) ON DELETE SET NULL,
    error         text,
    attempts      integer NOT NULL DEFAULT 0,
    worker_id     text,
    requested_at  timestamptz NOT NULL DEFAULT now(),
    started_at    timestamptz,
    finished_at   timestamptz,
    CONSTRAINT discovery_job_status_check
        CHECK (status IN ('queued','running','succeeded','failed'))
);

-- At most ONE live (queued/running) job per identity seed — dedups concurrent submissions while
-- letting failed/succeeded rows coexist so a seed can be retried or re-run.
CREATE UNIQUE INDEX IF NOT EXISTS discovery_job_live_seed_uq
    ON repop.discovery_job (seed_hash)
    WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS discovery_job_status_idx ON repop.discovery_job (status);

-- DB-backed daily spend ledger (replaces the file ledger in deployed/multi-machine runs).
CREATE TABLE IF NOT EXISTS repop.budget_ledger (
    day        date PRIMARY KEY,
    spent_usd  double precision NOT NULL DEFAULT 0
);

INSERT INTO repop.schema_migrations (version) VALUES ('0004_discovery_job')
    ON CONFLICT (version) DO NOTHING;

COMMIT;
