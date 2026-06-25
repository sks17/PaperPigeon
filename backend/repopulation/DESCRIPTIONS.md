# Grounded RAG Descriptions Contract (Phase 4) — see DISCOVERY.md + SCRAPING.md

How a repopulation run's nodes get a **grounded** `ai_description` (the frontend `about` text) from
the LLM, citing evidence and never inventing facts (AGENTS.md constraints 4–5). Strictly additive:
descriptions land only on a run's own non-legacy nodes, so the published legacy graph's `about` text
and the default served snapshot are unchanged.

## Pipeline data flow
`gather_evidence(session, node, run_id)  →  [evidence item]`            (main thread: Postgres + pgvector)
`build_description_prompt(node, evidence) → (system, user)`             (pure)
`LlmClient.complete_json(system, user)   → raw JSON`                    (main thread: OpenRouter, no tools)
`description_schema.validate(raw)        → NodeDescription | None`      (pure — injection backstop)
`build_rows.evaluate_description(...)    → (update | None, reason)`     (pure — grounding + legacy gate)
`loader.apply_description_updates(...)`                                  (main thread: UPDATE, guards legacy)

Orchestrated by `describe_run.describe_run(session, run_id, *, llm, generated_at, model, …)`. Rejected
descriptions are written to `repop.quarantine` (kind `description`), never dropped silently.

## Evidence item (the unit of grounding)
```
{ "id": int,        # 1-based, stable within one gather_evidence call — the citation handle
  "kind": str,      # affiliation | topics | paper | coauthor | related
  "text": str }     # a single grounded fact, framed as DATA in the prompt
```
`gather_evidence` (retrieve.py) assembles items in a **deterministic order** so prompts/citations are
stable: affiliation (AFFILIATED_WITH → institution) · topics (stored tags) · papers (≤8) · co-authors
(COAUTHORED_WITH, either direction, ≤8) · **related researchers** (pgvector nearest-neighbours within
the run, ≤k). Everything is scoped to `run_id` (run-membership join), so an unpublished run is
described only from its own snapshot. The `related` items are the RAG-over-pgvector step; when the run
has no embeddings (or no embedding for the node/model) they're simply absent and the description
grounds on the node's own stored facts.

## NodeDescription (`extraction/description_schema.py`, pure) — the backstop
```
@dataclass(frozen=True)
class NodeDescription:
    summary: str               # 1–3 sentences, grounded only in the evidence
    evidence: tuple[int, ...]  # cited evidence ids (deduped, sorted)
    confidence: float          # 0..1, the model's own self-rating

DESCRIPTION_JSON_SCHEMA: dict  # strict: required {summary, evidence, confidence}, additionalProperties:false
def validate(obj) -> NodeDescription | None
```
`validate` rejects (→ None, caller quarantines) non-dicts, missing required keys, **any extra/control
key** (e.g. an injected `tool_call` — the injection backstop), empty/over-long summaries, non-positive
or non-int evidence ids (bool excluded), and confidence outside 0..1 (bool/non-numeric excluded).

## Grounding + legacy gate (`descriptions/build_rows.py`, pure)
```
def evaluate_description(node, description, evidence, *, generated_at, model, min_confidence=0.5)
    -> (update: dict | None, reason: str | None)
```
Rejects with a reason — `legacy-preserve` (node.description_model == `legacy_dynamodb` → never
overwrite), `low-confidence` (< min_confidence), or `ungrounded` (no citations, **or** a cited id that
wasn't in the evidence shown to the model = a hallucinated citation → the whole description is
dropped). On accept, the update is:
```
{ "node_id", "ai_description": summary, "description_model": model,
  "description_generated_at": generated_at, "description_evidence": [cited evidence items] }
```
`description_evidence` stores exactly the cited items, so every persisted description carries its
grounding for audit. The model's `confidence` is a gate only — it is **not** written to `node.confidence`
(that column is node-level provenance, not description-level).

## Persistence (`loader.apply_description_updates`, main thread)
The node upsert is `ON CONFLICT DO NOTHING`, so descriptions need an explicit UPDATE. This function
sets only the four description fields and **never** touches a `legacy_dynamodb` description
(`description_model IS DISTINCT FROM 'legacy_dynamodb'`) — double-guarding snapshot isolation alongside
the pure gate. Idempotent (re-applying rewrites identical values).

## Orchestration invariants (`describe_run.describe_run`)
- **Candidates**: run-member nodes of `kinds` (default `("researcher",)`) that are not legacy and not
  already described by this `model` → **idempotent** (re-running a seed is a no-op).
- **Additive / isolated**: only the run's nodes are described; `graph_from_db(None)` (published legacy)
  is unchanged; the run's `about` is visible only via `GET /api/graph/data?run=<id>` until publish.
- **Quarantine-don't-crash**: `no-evidence`, `llm-error:…`, `invalid-extraction`, `low-confidence`,
  `ungrounded` each quarantine the node and continue.
- **Budget-aware**: the LLM call is charged through `clients/budget.py`; `limit` caps nodes per run.

## Node kinds
`describe_run(kinds=…)` defaults to `("researcher",)`. Lab nodes are supported too (`gather_evidence`
dispatches on `node.kind`): a lab grounds on its scraped `self_description`, PI, `research_areas`, and
`MEMBER_OF` members — no pgvector (labs aren't embedded). The prompt already frames "a lab named X",
so no per-kind prompt is needed. Because `build_lab_rows` reuses legacy lab ids, a published lab can
appear in a run's membership; `describe_run` excludes any node belonging to the **published run**, so
those are never re-described (snapshot isolation).

## Drivers
- `scripts/describe.py --institution "…" [--topic …] [--min-confidence 0.5] [--limit N]
  [--no-embeddings] [--publish]` — boots local PG, loads+publishes the legacy graph, runs a Phase-2
  repopulation (embedded so pgvector evidence exists), then `describe_run` over researchers; reports
  how many gained a grounded `about`.
- `scripts/scrape_labs.py --institution "…" --describe [--min-confidence 0.5] [--publish]` — the
  Phase-3 scrape batch with a Phase-4 tail: after scraping labs into the run, describes researchers
  **and** labs (`kinds=("researcher","lab")`).

## Deferred (future passes)
- **Frontend surfacing** of `?run=<id>` snapshots (the existing UI still renders the published graph;
  lab `about` is exposed via a separate read, not the 4-field graph node).
- **Promote**: deliberately copying a repop run's grounded description onto a published legacy node.
