"""Paper Pigeon — Repopulation Engine (strictly-additive layer).

Phase 1 (Foundation): import the existing static graph into Postgres+pgvector and serve it
back through a new API with structurally-identical output. No live repopulation yet.

Package layout:
  migrations/     SQL migrations (authored + run by the main thread)
  models/         SQLAlchemy models mirroring migrations/0001_initial.sql        [P1-T01]
  serializers/    rows -> frontend {nodes, links}                                [P1-T02]
  importer/       existing graph_cache.json -> row dicts                         [P1-T03]
  sources/        pure parsers over OpenAlex/ROR fixtures (no live HTTP)         [P1-T04]
  tests/          pytest suites (authored by Cursor, run by the main thread)
SCHEMA.md is the row-dict + output contract that the pure functions implement against.
"""
