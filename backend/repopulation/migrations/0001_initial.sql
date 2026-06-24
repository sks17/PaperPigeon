-- Paper Pigeon — Repopulation Engine — Migration 0001 (initial schema)
--
-- ADDITIVE-ONLY. This migration only CREATEs, all guarded with IF NOT EXISTS, inside a
-- dedicated `repop` schema. It touches nothing the existing app uses (the current graph is
-- served from a static cache / DynamoDB and never reads Postgres), so the existing graph is
-- unaffected by definition. There are NO DROP/destructive ALTER statements — the
-- additive-only invariant (asserted by tests/test_migration_additive.py) must hold forever
-- for 0001; future changes go in new numbered migrations.
--
-- Run by the MAIN THREAD only:  psql "$DATABASE_URL" -f backend/repopulation/migrations/0001_initial.sql
-- Authoritative data model: 01-product-overview.md → Data model. Row-dict + serializer
-- contracts that Cursor implements against live in backend/repopulation/SCHEMA.md.

BEGIN;

CREATE SCHEMA IF NOT EXISTS repop;
CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector, for repop.embedding

-- ─── migration bookkeeping ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS repop.schema_migrations (
    version     text PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now()
);

-- ─── repopulation_run: scopes relevance + provenance to one seed/run ─────────
-- Relevance is RELATIVE to a seed, so it is stored per-run, never as a global node property.
CREATE TABLE IF NOT EXISTS repop.repopulation_run (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    seed        jsonb NOT NULL,
    status      text  NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','running','succeeded','failed','quarantine')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ─── source_record: the provenance spine. Every node and edge points to one. ─
CREATE TABLE IF NOT EXISTS repop.source_record (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source       text NOT NULL
                 CHECK (source IN ('openalex','crossref','arxiv','pubmed','scrape','ai','legacy_cache')),
    source_url   text,
    retrieved_at timestamptz NOT NULL DEFAULT now(),
    confidence   double precision CHECK (confidence >= 0 AND confidence <= 1),
    evidence     text,            -- affiliation string / API field / scraped selector
    run_id       bigint REFERENCES repop.repopulation_run(id) ON DELETE SET NULL,
    raw_s3_key   text,            -- replayability: raw payload stored in S3 before transform
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- ─── node: unified table for all node kinds ─────────────────────────────────
-- Rendered, type-specific fields (researcher: advisor/contact_info/labs/standing/papers/tags)
-- live in `attributes` jsonb; first-class columns are reserved for identity/dedup + AI desc.
CREATE TABLE IF NOT EXISTS repop.node (
    id                        text PRIMARY KEY,            -- existing researcher_id / lab_id preserved
    kind                      text NOT NULL
                              CHECK (kind IN ('researcher','lab','institution','department','topic','venue','paper')),
    name                      text NOT NULL,
    val                       smallint NOT NULL,           -- frontend convention: 1=researcher, 2=lab
    -- identity / dedup keys (dedup order: ORCID → OpenAlex → ROR → normalized name)
    orcid                     text,
    openalex_id               text,
    ror                       text,
    normalized_name           text,
    attributes                jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- grounded AI description (every node type; legacy `about` backfilled as legacy_dynamodb)
    ai_description            text,
    description_model         text,
    description_generated_at  timestamptz,
    description_evidence      jsonb,
    confidence                double precision CHECK (confidence >= 0 AND confidence <= 1),
    source_record_id          bigint REFERENCES repop.source_record(id) ON DELETE SET NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);
-- Partial-unique dedup indexes (only enforce where the key is present).
CREATE UNIQUE INDEX IF NOT EXISTS node_orcid_uq       ON repop.node (orcid)       WHERE orcid IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS node_openalex_uq    ON repop.node (openalex_id) WHERE openalex_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS node_ror_uq         ON repop.node (ror)         WHERE ror IS NOT NULL;
CREATE INDEX        IF NOT EXISTS node_kind_idx       ON repop.node (kind);
CREATE INDEX        IF NOT EXISTS node_norm_name_idx  ON repop.node (normalized_name);

-- ─── edge: typed, directed, WEIGHTED, provenance-bearing ────────────────────
-- Rich types are stored here; the serializer maps the render-relevant ones down to the
-- existing frontend link.type vocabulary (COAUTHORED_WITH→paper, ADVISED_BY→advisor,
-- MEMBER_OF→researcher_lab). See SCHEMA.md → Edge type mapping.
CREATE TABLE IF NOT EXISTS repop.edge (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    src_id            text NOT NULL REFERENCES repop.node(id) ON DELETE CASCADE,
    dst_id            text NOT NULL REFERENCES repop.node(id) ON DELETE CASCADE,
    type              text NOT NULL
                      CHECK (type IN ('AUTHORED','MEMBER_OF','AFFILIATED_WITH','PART_OF',
                                      'COAUTHORED_WITH','ADVISES','ADVISED_BY','COLLABORATES_WITH',
                                      'WORKS_ON','FOCUSES_ON','CITES','ALUMNUS_OF','SIMILAR_TO')),
    weight            double precision NOT NULL DEFAULT 1.0,
    directed          boolean NOT NULL DEFAULT true,
    attributes        jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence        double precision CHECK (confidence >= 0 AND confidence <= 1),
    source_record_id  bigint REFERENCES repop.source_record(id) ON DELETE SET NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    -- idempotency: re-running a seed must not create duplicate edges
    CONSTRAINT edge_uq UNIQUE (src_id, dst_id, type)
);
CREATE INDEX IF NOT EXISTS edge_src_idx  ON repop.edge (src_id);
CREATE INDEX IF NOT EXISTS edge_dst_idx  ON repop.edge (dst_id);
CREATE INDEX IF NOT EXISTS edge_type_idx ON repop.edge (type);

-- ─── relevance: query-scoped score per (node, run) ──────────────────────────
CREATE TABLE IF NOT EXISTS repop.relevance (
    node_id     text   NOT NULL REFERENCES repop.node(id) ON DELETE CASCADE,
    run_id      bigint NOT NULL REFERENCES repop.repopulation_run(id) ON DELETE CASCADE,
    score       double precision NOT NULL,
    components  jsonb,             -- {cosine, recency, volume, weights} for explainability
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, run_id)
);
CREATE INDEX IF NOT EXISTS relevance_run_idx ON repop.relevance (run_id);

-- ─── embedding: pgvector retrieval store (populated in Phase 2; table is additive now) ──
CREATE TABLE IF NOT EXISTS repop.embedding (
    node_id     text   NOT NULL REFERENCES repop.node(id) ON DELETE CASCADE,
    model       text   NOT NULL,
    embedding   vector(1536),      -- adjust dim per chosen embedding model in a later migration
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (node_id, model)
);

INSERT INTO repop.schema_migrations (version) VALUES ('0001_initial')
    ON CONFLICT (version) DO NOTHING;

COMMIT;
