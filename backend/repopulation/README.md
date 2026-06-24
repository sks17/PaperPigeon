# Repopulation Package Layout

Strictly-additive layer used in Phase 1 (Foundation): import the existing static graph into Postgres+pgvector and serve it back through a new API with structurally-identical output.

## Package layout

- `migrations/`: SQL migrations (authored + run by the main thread)
- `models/`: SQLAlchemy models mirroring `migrations/0001_initial.sql` [P1-T01]
- `serializers/`: rows -> frontend `{nodes, links}` [P1-T02]
- `importer/`: existing `graph_cache.json` -> row dicts [P1-T03]
- `sources/`: pure parsers over OpenAlex/ROR fixtures (no live HTTP) [P1-T04]
- `tests/`: pytest suites (authored by Cursor, run by the main thread)

For the data contract, see `SCHEMA.md`.
For the foundation schema definition, see `migrations/0001_initial.sql`.
