-- Paper Pigeon — Repopulation Engine — Migration 0003 (quarantine audit)
--
-- ADDITIVE-ONLY (enforced by tests/test_migration_additive.py across all migrations). Records the
-- low-confidence / conflicting scrape records that "quarantine, don't crash" drops from a run's
-- accepted ImportRows — so they never enter a snapshot but remain auditable/reviewable.
--
-- Run by the MAIN THREAD only:  psql "$DATABASE_URL" -f backend/repopulation/migrations/0003_quarantine.sql

BEGIN;

CREATE TABLE IF NOT EXISTS repop.quarantine (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id      bigint REFERENCES repop.repopulation_run(id) ON DELETE CASCADE,
    kind        text NOT NULL,            -- 'lab' | 'member' | 'edge'
    payload     jsonb NOT NULL,           -- the dropped item
    reason      text NOT NULL,            -- why it was quarantined
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS quarantine_run_idx ON repop.quarantine (run_id);

INSERT INTO repop.schema_migrations (version) VALUES ('0003_quarantine')
    ON CONFLICT (version) DO NOTHING;

COMMIT;
