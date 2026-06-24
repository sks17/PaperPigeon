-- Paper Pigeon — Repopulation Engine — Migration 0002 (run-membership snapshots)
--
-- ADDITIVE-ONLY (same invariant as 0001; enforced by tests/test_migration_additive.py over all
-- migrations). Adds per-run snapshot membership so a repopulation run is invisible to the served
-- graph until published — the existing graph keeps rendering exactly as today.
--
-- run_node / run_edge: which nodes/edges constitute a run's snapshot. A node/edge can belong to
-- many runs (dedup means the same researcher appears across runs), so membership is a join, not a
-- column on node/edge. app_state holds the `published_run_id` pointer the default serve path uses.
--
-- Run by the MAIN THREAD only:  psql "$DATABASE_URL" -f backend/repopulation/migrations/0002_run_membership.sql

BEGIN;

CREATE TABLE IF NOT EXISTS repop.run_node (
    run_id   bigint NOT NULL REFERENCES repop.repopulation_run(id) ON DELETE CASCADE,
    node_id  text   NOT NULL REFERENCES repop.node(id) ON DELETE CASCADE,
    PRIMARY KEY (run_id, node_id)
);
CREATE INDEX IF NOT EXISTS run_node_node_idx ON repop.run_node (node_id);

CREATE TABLE IF NOT EXISTS repop.run_edge (
    run_id   bigint NOT NULL REFERENCES repop.repopulation_run(id) ON DELETE CASCADE,
    edge_id  bigint NOT NULL REFERENCES repop.edge(id) ON DELETE CASCADE,
    PRIMARY KEY (run_id, edge_id)
);
CREATE INDEX IF NOT EXISTS run_edge_edge_idx ON repop.run_edge (edge_id);

-- Small key/value table for app pointers (e.g. published_run_id). Serving defaults to this run.
CREATE TABLE IF NOT EXISTS repop.app_state (
    key    text PRIMARY KEY,
    value  text
);

INSERT INTO repop.schema_migrations (version) VALUES ('0002_run_membership')
    ON CONFLICT (version) DO NOTHING;

COMMIT;
