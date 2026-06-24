# Cursor Task Pool — parallel, scoped, file-disjoint

Up to **10 Cursor agents loop** here, each grabbing the next `open` task. **Read `AGENTS.md`,
`backend/repopulation/SCHEMA.md`, and `backend/repopulation/DISCOVERY.md` first.** The stubs you
fill already exist with signatures + contracts.

## Rules (non-negotiable)
- **Disjoint `Files allowed`:** no two `open`/`claimed` tasks touch the same file.
- **No meta work:** ❌ integrating external APIs (no HTTP clients / `requests`/`httpx`/`urllib`, no
  keys) · ❌ running commands · ❌ planning/architecture · ❌ schema/migration changes. The main
  thread wires APIs/clients, runs migrations, runs every test.
- **Pure functions stay pure:** no DB/file/network/wall-clock in transforms (`build_rows`,
  `relevance/score`). Pass `current_year` in; never read the clock.
- **Strictly additive:** never change what the existing graph renders.
- **Acceptance checkable WITHOUT running anything.** Finish → set `done` → grab next `open`.

---

## Phase 1 — COMPLETE ✅
All P1 tasks merged and verified (importer/serializer/models/parsers + tests; DB load/serve; new
FastAPI endpoint; 27 backend tests + 1 Playwright e2e green).

## Phase 2 — Repopulation v1 (live discovery, relevance) — see DISCOVERY.md

### P2-T01: build-import-rows        [status: done]
Layer: engine · Branch: agent/repopulation-engine · Depends on: —
Goal:            Implement `build_import_rows(institution, authors, seed, run_key, source_keys)`
                 per DISCOVERY.md — parsed OpenAlex/ROR dataclasses → ImportRows.
Files allowed:   backend/repopulation/discovery/build_rows.py
Files forbidden: everything else.
Acceptance:      Emits institution/researcher/topic/paper nodes (NODE_VAL convention, dedup keys
                 orcid/openalex_id/ror/normalized_name) and AFFILIATED_WITH / AUTHORED / WORKS_ON
                 (weight=topic share) / COAUTHORED_WITH (weight=#joint works) edges, each with a
                 source_record_key; one source_record per source ('openalex','ror'); relevance=[].
                 Pure (no HTTP/DB/clock). Verifiable by reading against DISCOVERY.md + SCHEMA.md §1–2.
Do NOT:          import clients/* or any HTTP lib · access a DB · read the clock · edit the loader.

### P2-T02: relevance-scoring        [status: done]
Layer: engine · Branch: agent/ai-descriptions-rag · Depends on: —
Goal:            Implement `cosine`, `recency_decay`, `score_relevance` per DISCOVERY.md.
Files allowed:   backend/repopulation/relevance/score.py
Files forbidden: everything else.
Acceptance:      score = w1*cosine + w2*recency_decay(current_year passed in) + w3*log1p(volume);
                 returns relevance_row dicts (SCHEMA.md §2) scoped to run_key with `components`
                 populated; cosine handles zero/empty vectors; no wall-clock. Pure.
Do NOT:          read the clock · access network/DB · import clients/*.

### P2-T03: test-build-rows        [status: done]
Layer: engine (test) · Branch: agent/repopulation-engine · Depends on: —
Goal:            Test build_import_rows over the existing parser fixtures.
Files allowed:   backend/repopulation/tests/test_build_rows.py
Files forbidden: everything else.
Acceptance:      Parses tests/fixtures/openalex_author_fixture.json + ror_organization_fixture.json
                 via the existing parsers, calls build_import_rows, and asserts: node kinds/vals
                 correct; every node/edge has a source_record_key resolving to a returned
                 source_record; COAUTHORED_WITH weight = #joint works; WORKS_ON present; dedup keys
                 populated; relevance == []. No DB/network.
Do NOT:          hit a DB/network · edit impl files.

### P2-T04: test-relevance        [status: done]
Layer: engine (test) · Branch: agent/ai-descriptions-rag · Depends on: —
Goal:            Test the relevance scoring math.
Files allowed:   backend/repopulation/tests/test_relevance_score.py
Files forbidden: everything else.
Acceptance:      cosine of identical vectors == 1.0, orthogonal == 0.0, zero-vector safe; recency
                 decays with age and is 0.0 for unknown year; score_relevance returns one row per
                 node with score in a sane range and components summing per the weights; current_year
                 passed in (no clock). Inline fixtures.
Do NOT:          read the clock · hit a DB/network · edit impl files.

### P2-T05: test-snapshot-isolation        [status: done]
Layer: graph-data (test) · Branch: agent/graph-schema · Depends on: —
Goal:            Prove a second (unpublished) run does not change the default served graph.
Files allowed:   backend/repopulation/tests/test_snapshot_isolation.py
Files forbidden: everything else.
Acceptance:      Using pgserver (pytest.importorskip) + all migrations: load the legacy cache (auto-
                 published) then load a small SECOND ImportRows under a different run/seed; assert
                 graph_from_db(session) (default) still returns the legacy 323/1043; assert
                 graph_from_db(session, run_id=<second>) returns ONLY the second run's nodes/edges;
                 assert publish_run(second) flips the default. Mirror the pgserver fixture pattern in
                 tests/test_api_graph_contract.py.
Do NOT:          edit impl files · integrate APIs.

---

## Phase 2b — pure-code follow-ups + client test coverage (main thread built the live clients)
The live clients (clients/*), run.py, queue.py are DONE and validated against the real OpenAlex/ROR/
OpenRouter APIs. These tasks are pure-code refinements + tests Cursor authors and the main thread runs.
Clients take an injectable `http` (duck-typed get_json/post_json) — tests pass a STUB http returning
canned dicts; never make real network calls.

### P2-T06: relevance-volume-normalization        [status: done]
Layer: engine · Branch: agent/ai-descriptions-rag · Depends on: —
Goal:            Normalize the volume term so the three relevance components are comparable.
Files allowed:   backend/repopulation/relevance/score.py
Files forbidden: everything else.
Acceptance:      log1p(volume) currently is unbounded (~0–8) while cosine/recency are [0,1], so volume
                 dominates the weighted score. Normalize volume to [0,1] WITHIN the scored batch
                 (e.g. divide log1p(volume) by the max log1p(volume) across node_meta; 0 when the max
                 is 0). Keep `components` reporting the normalized value + raw. Pure, no clock.
                 Update tests/test_relevance_score.py expectations accordingly.
Do NOT:          read the clock · network/DB · import clients/*.

### P2-T07: test-http-client        [status: done]
Layer: infra (test) · Branch: agent/infra-cicd · Depends on: —
Goal:            Unit-test the polite HTTP client behaviors with httpx.MockTransport.
Files allowed:   backend/repopulation/tests/test_http_client.py
Files forbidden: everything else.
Acceptance:      Build HttpClient with a httpx.MockTransport (inject via the underlying client) +
                 LocalRawStore in tmp_path + a fake sleep/monotonic. Assert: SSRFError on non-https
                 and non-allowlisted host; retry on 503 then success (sleep called); raw-store
                 write-through then a second call is a cache hit (live_calls unchanged, cache_hits++).
Do NOT:          make real network calls · edit impl files.

### P2-T08: test-openalex-client        [status: done]
Layer: engine (test) · Branch: agent/backend-api · Depends on: —
Goal:            Budget-guard + pagination/assembly tests via a STUB http.
Files allowed:   backend/repopulation/tests/test_openalex_client.py
Files forbidden: everything else.
Acceptance:      Inject a stub http recording every (url, params). Assert iter_authors/iter_works
                 build `filter=last_known_institutions.id:...` / `authorships.institutions.id:...`,
                 send `cursor` + `per-page`=200 + `select`, and NEVER send a `search` param (budget).
                 Assert cursor pagination follows meta.next_cursor and stops at max_pages.
                 Assert discover_authors attaches works to authors via authorships (recent_works).
Do NOT:          real network · edit impl files.

### P2-T09: test-run-repopulation        [status: done]
Layer: engine (test) · Branch: agent/repopulation-engine · Depends on: —
Goal:            End-to-end run_repopulation with stub clients + pgserver.
Files allowed:   backend/repopulation/tests/test_run_repopulation.py
Files forbidden: everything else.
Acceptance:      pgserver (importorskip) + all migrations + legacy loaded. Stub ror.resolve →
                 RorOrganization, openalex.get_institution_by_ror/discover_authors → canned dicts,
                 embeddings=None. Run run_repopulation; assert: a run row is 'succeeded'; default
                 graph_from_db still 323/1043 (unpublished); graph_from_db(run_id) shows the discovered
                 researchers; relevance rows == #researchers; re-running the same seed is idempotent.
Do NOT:          real network · edit impl files.

### P2-T10: ror-canonical-name        [status: done]
Layer: engine · Branch: agent/graph-schema · Depends on: —
Goal:            Prefer the ROR `ror_display` name (fixes localized names, e.g. "Universidad de Washington").
Files allowed:   backend/repopulation/sources/ror_parse.py, backend/repopulation/tests/test_ror_parse.py
Files forbidden: everything else.
Acceptance:      _canonical_name prefers the names entry whose `types` include "ror_display", then
                 "label", then first; test with a fixture having multiple localized names asserts the
                 ror_display English name wins. Pure.
Do NOT:          network/DB · import clients/*.

---

## Phase 3 — Lab layer (scraping + grounded extraction) — see SCRAPING.md + DISCOVERY.md
The main thread builds the network/LLM layer (clients/ssrf.py, clients/http.get_text, clients/llm.py,
scraping/{robots,discovery,fetch}.py, extraction/extract_labs.py, scrape_run.py, the CLI). These tasks are
the PURE transforms + their tests. Stubs exist with signatures + contracts.

### P3-T01: clean-html        [status: done]
Layer: engine · Branch: agent/scraping-and-ai-extraction · Depends on: —
Goal:            Implement `clean_html(html, url)` per SCRAPING.md §1 (trafilatura main-content extraction).
Files allowed:   backend/repopulation/scraping/clean.py
Files forbidden: everything else.
Acceptance:      Returns {url,title,text,anchors,chunks}; strips scripts/nav/boilerplate via trafilatura;
                 Docling path only when PREFER_DOCLING truthy; chunks are deterministic. Pure — no network,
                 no script execution, no link-following; injected instructions in HTML stay inert text.
Do NOT:          import clients/* or any HTTP lib · network/DB/clock.

### P3-T02: lab-extraction-schema        [status: done]
Layer: engine · Branch: agent/scraping-and-ai-extraction · Depends on: —
Goal:            Implement LAB_JSON_SCHEMA + `validate(obj)` per SCRAPING.md §2.
Files allowed:   backend/repopulation/extraction/lab_schema.py
Files forbidden: everything else.
Acceptance:      LAB_JSON_SCHEMA is a strict JSON schema (required: lab_name, members, research_areas,
                 confidence; optional pi/self_description/source_anchor; NO extra/control fields).
                 validate(obj) returns a LabExtraction on match else None (wrong types / missing required /
                 extra keys -> None). This is the injection backstop. Pure.
Do NOT:          network/DB/LLM/clock · import clients/*.

### P3-T03: build-lab-rows        [status: done]
Layer: engine · Branch: agent/scraping-and-ai-extraction · Depends on: —
Goal:            Implement `build_lab_rows(...)` per SCRAPING.md §3.
Files allowed:   backend/repopulation/discovery/build_lab_rows.py
Files forbidden: everything else.
Acceptance:      Returns {accepted: ImportRows, quarantined:[{kind,payload,reason}]}. Members reconciled to
                 researcher_ids by normalized name (unmatched -> quarantine, no edge); labs merged with legacy
                 lab_ids when names match, deduped by normalize(name)+dept; lab confidence < min_confidence OR
                 no source_anchor -> quarantine whole lab. Emits lab/department nodes + MEMBER_OF/PART_OF/
                 FOCUSES_ON edges; every node/edge has a source_record_key (source='scrape') + weight +
                 confidence. Pure, deterministic, idempotent.
Do NOT:          import clients/* or any HTTP lib · network/DB/clock · edit the loader.

### P3-T04: test-clean        [status: done]
Layer: engine (test) · Branch: agent/scraping-and-ai-extraction · Depends on: —
Goal:            Test clean_html on fixture HTML.
Files allowed:   backend/repopulation/tests/test_clean.py, backend/repopulation/tests/fixtures/lab_page*.html
Files forbidden: everything else.
Acceptance:      A realistic faculty/lab-page fixture (with nav/script/footer boilerplate + a members list +
                 a self-description) → clean_html returns main text containing the description + member names
                 and NOT the boilerplate/script; chunks non-empty; an injection-laced fixture
                 ("<!-- ignore all instructions -->") cleans without executing anything (text only).
Do NOT:          network/DB · edit impl files.

### P3-T05: test-lab-schema        [status: done]
Layer: engine (test) · Branch: agent/scraping-and-ai-extraction · Depends on: —
Goal:            Test the schema validator (injection backstop).
Files allowed:   backend/repopulation/tests/test_lab_schema.py
Files forbidden: everything else.
Acceptance:      validate() accepts a well-formed extraction → LabExtraction; rejects (None) on: missing
                 required field, wrong type, and EXTRA/control keys (e.g. {"tool_call": ...}); confidence
                 coerced/validated to 0..1. Pure.
Do NOT:          network/DB/LLM · edit impl files.

### P3-T06: test-build-lab-rows        [status: done]
Layer: engine (test) · Branch: agent/repopulation-engine · Depends on: —
Goal:            Test reconciliation + quarantine + legacy merge.
Files allowed:   backend/repopulation/tests/test_build_lab_rows.py
Files forbidden: everything else.
Acceptance:      With a small researcher_set + legacy_labs: a matched member → MEMBER_OF edge; an unmatched
                 member → quarantined (no edge); a low-confidence / no-anchor lab → quarantined (absent from
                 accepted); a lab named like a legacy lab reuses its lab_id; every accepted node/edge has a
                 source_record_key + provenance + weight. No DB/network.
Do NOT:          hit a DB/network · edit impl files.

### P3-T07: test-ssrf-scrape        [status: done]
Layer: infra (test) · Branch: agent/security-hardening · Depends on: —
Goal:            Test the scraper SSRF validator with a mocked resolver.
Files allowed:   backend/repopulation/tests/test_ssrf_scrape.py
Files forbidden: everything else.
Acceptance:      validate_scrape_url (inject resolver=fake getaddrinfo): blocks non-https; blocks off-domain
                 host; blocks hosts resolving to private/loopback/link-local/metadata IPs (127.0.0.1, 10.x,
                 192.168.x, 169.254.169.254, ::1); allows a public IP under an allowed domain (and a subdomain
                 like cs.washington.edu under washington.edu).
Do NOT:          real DNS/network · edit impl files.
