# Cursor Task Pool — parallel, scoped, file-disjoint

Up to **10 Cursor agents loop** here, each grabbing the next `open` task. **Read `AGENTS.md` and
`backend/repopulation/SCHEMA.md` first.** The schema (`backend/repopulation/migrations/0001_initial.sql`)
and `SCHEMA.md` are the source of truth; the stubs you fill already exist with signatures + contracts.

## Rules (non-negotiable — see 03-agent-structure.md)
- **Disjoint `Files allowed`:** no two `open`/`claimed` tasks may touch the same file.
- **No meta work:** ❌ integrating external APIs (no HTTP clients / no `requests`/`httpx`/`urllib`,
  no API keys) · ❌ running commands (installs/migrations/deploys/tests) · ❌ planning/architecture ·
  ❌ schema/migration changes. The main thread wires APIs, runs migrations, and runs every test.
- **Strictly additive:** never change what the existing graph renders. Do not edit existing
  `src/`, `backend/app.py`, `backend/graph_core.py`, or any existing file outside your `Files allowed`.
- **Pure functions stay pure:** no DB, file, or network I/O in the importer/serializer/parsers.
- **Acceptance must be checkable WITHOUT running anything.** Finish → set `done` → grab next `open`.

## Task format
```
### TASK-ID: short-name        [status: open | claimed | done]
Layer: … | Branch: agent/… | Depends on: …
Goal / Files allowed / Files forbidden / Acceptance / Do NOT
```

---

### P1-T01: orm-models        [status: done]
Layer: graph-schema · Branch: agent/graph-schema · Depends on: —
Goal:            SQLAlchemy 2.0 typed models mirroring `migrations/0001_initial.sql` exactly.
Files allowed:   backend/repopulation/models/nodes.py, backend/repopulation/models/edges.py,
                 backend/repopulation/models/provenance.py
Files forbidden: the SQL migration (source of truth — read only), everything else.
Acceptance:      Each table in 0001_initial.sql (node, edge, source_record, repopulation_run,
                 relevance, embedding) has a model with matching column names, types, nullability,
                 CHECK constraints, PKs, the UNIQUE(src_id,dst_id,type) edge constraint, and the
                 partial-unique dedup indexes (orcid/openalex_id/ror). No engine/session/connection
                 code, no DDL execution. Reviewable by reading against the SQL.
Do NOT:          open DB connections · run migrations · edit the SQL · integrate APIs.

### P1-T02: graph-serializer        [status: done]
Layer: api · Branch: agent/backend-api · Depends on: —
Goal:            Implement `serialize_graph(nodes, edges, relevance_by_node)` per SCHEMA.md §1/§3/§4.
Files allowed:   backend/repopulation/serializers/graph_data.py
Files forbidden: everything else.
Acceptance:      Researcher nodes emit all 12 keys (null where absent) in the documented shape; lab
                 nodes emit exactly 4 keys; edges map rich→render via RICH_TO_RENDER_LINK_TYPE and
                 unmapped types are skipped; node + link order preserved. Pure (no I/O). Verifiable
                 by reading against SCHEMA.md §4.
Do NOT:          read files/DB/network · change the contract · edit the importer.

### P1-T03: cache-importer        [status: done]
Layer: engine · Branch: agent/repopulation-engine · Depends on: —
Goal:            Implement `cache_to_rows(graph)` per SCHEMA.md §1/§2/§3 (legacy import).
Files allowed:   backend/repopulation/importer/cache_to_rows.py
Files forbidden: everything else.
Acceptance:      Returns the ImportRows dict: one legacy run_row + one legacy source_record_row;
                 researcher/lab node_rows (attributes per SCHEMA.md §2, about→ai_description with
                 model "legacy_dynamodb", influence NOT stored on the node); edge_rows via
                 RENDER_TO_RICH_EDGE_TYPE (weight 1.0, directed); relevance_rows only for
                 researchers whose influence is not None. Order-preserving; idempotent at data level.
                 Pure (receives the parsed dict; no file/DB/network).
Do NOT:          read the cache file itself · touch the serializer · integrate APIs.

### P1-T04: source-parsers        [status: done]
Layer: engine · Branch: agent/repopulation-engine · Depends on: —
Goal:            Pure parsers over SAVED OpenAlex/ROR fixtures → internal @dataclass types.
Files allowed:   backend/repopulation/sources/openalex_parse.py,
                 backend/repopulation/sources/ror_parse.py,
                 backend/repopulation/tests/fixtures/*.json (add small, realistic fixtures)
Files forbidden: everything else.
Acceptance:      Dataclasses + parse functions extract the fields named in each stub (OpenAlex:
                 id, ids.orcid, display_name, last_known_institution id/ror, topics, recent works,
                 counts, abstract_inverted_index handling; ROR: id, name, country, aliases,
                 relationships). NO HTTP client import, NO keys, NO rate-limit logic. Reviewable
                 by reading parser vs. fixture.
Do NOT:          import requests/httpx/urllib · add auth · call the network.

### P1-T05: test-graph-contract        [status: done]
Layer: api (test) · Branch: agent/backend-api · Depends on: —
Goal:            Round-trip golden test: serialize(cache_to_rows(cache)) reproduces the cache.
Files allowed:   backend/repopulation/tests/test_graph_contract.py
Files forbidden: everything else.
Acceptance:      Loads public/graph_cache.json (read-only), runs it through cache_to_rows then
                 serialize_graph (passing the legacy relevance map), and asserts STRUCTURAL equality
                 with the original — same node order, same link order, same fields/values (compare
                 parsed objects; key order irrelevant). Also assert counts 298 researcher / 25 lab /
                 paper 633 / advisor 190 / researcher_lab 220.
Do NOT:          touch a DB · edit impl files · assert on JSON byte/key order.

### P1-T06: test-provenance-integrity        [status: done]
Layer: graph-data (test) · Branch: agent/graph-schema · Depends on: —
Goal:            Assert provenance + edge integrity on importer output.
Files allowed:   backend/repopulation/tests/test_provenance_integrity.py
Files forbidden: everything else.
Acceptance:      On cache_to_rows(public/graph_cache.json): every node_row and edge_row has a
                 `source_record_key` that resolves to a returned source_record_row; every edge_row's
                 src_id/dst_id exists among node_rows (no dangling edges); every edge_row has a
                 rich `type`, a numeric `weight`, and `directed` set.
Do NOT:          touch a DB · edit impl files.

### P1-T07: test-importer-idempotency        [status: done]
Layer: engine (test) · Branch: agent/repopulation-engine · Depends on: —
Goal:            Assert the importer is order-preserving and idempotent at the data level.
Files allowed:   backend/repopulation/tests/test_importer_idempotency.py
Files forbidden: everything else.
Acceptance:      cache_to_rows(x) == cache_to_rows(x) (stable); node 'id' values are unique;
                 (src_id,dst_id,type) tuples are unique across edge_rows; node/edge order matches
                 the input cache order. Use the real cache and/or a small inline fixture.
Do NOT:          touch a DB · edit impl files.

### P1-T08: test-migration-additive        [status: done]
Layer: infra (test) · Branch: agent/infra-cicd · Depends on: —
Goal:            Static guard that migration 0001 is additive-only.
Files allowed:   backend/repopulation/tests/test_migration_additive.py
Files forbidden: everything else.
Acceptance:      Reads migrations/0001_initial.sql as text and asserts NO **statement-level**
                 destructive DDL — match `DROP TABLE|SCHEMA|INDEX|COLUMN|CONSTRAINT`, `TRUNCATE`,
                 `DELETE FROM`, `ALTER ... DROP`. MUST NOT false-positive on FK referential actions
                 `ON DELETE CASCADE` / `ON DELETE SET NULL` (those are not destructive). Also assert
                 every CREATE is guarded (`IF NOT EXISTS`) and all objects are under the `repop` schema.
Do NOT:          execute SQL · connect to a DB · edit the migration.

### P1-T09: e2e-graph-smoke        [status: done]
Layer: frontend (test) · Branch: agent/backend-api · Depends on: —
Goal:            Playwright smoke proving the existing graph still renders (run by main thread).
Files allowed:   e2e/graph-smoke.spec.ts
Files forbidden: everything else (do not add deps or configs — the main thread wires Playwright).
Acceptance:      Spec navigates to the app root, waits for the graph canvas, asserts the graph data
                 request to /api/graph/data returns ok and a non-empty nodes array, and asserts no
                 uncaught console errors. Written against @playwright/test API; selectors documented.
Do NOT:          run the test · install Playwright · change vite/eslint config · edit src/.
