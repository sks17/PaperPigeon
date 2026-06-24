# Scraping + Lab Extraction Contract (Phase 3)

How scraped lab pages become lab/department nodes + `MEMBER_OF`/`PART_OF`/`FOCUSES_ON` edges in the
SAME `ImportRows` shape the loader ingests (SCHEMA.md §1–2). The pure transforms (`scraping/clean.py`,
`extraction/lab_schema.py`, `discovery/build_lab_rows.py`) bind to this doc. Main-thread code owns all
fetching + the live LLM call; lower tiers stay pure (no HTTP/DB/network/clock/LLM).

## Pipeline data flow
`fetch (main) → CleanedPage (clean.py) → LabExtraction (extract via LLM, validated by lab_schema) →
build_lab_rows(extractions, institution, researcher_set, legacy_labs) → {accepted: ImportRows, quarantined}`

## 1. CleanedPage — `scraping/clean.py` (pure)
```
def clean_html(html: str, url: str) -> dict   # CleanedPage
# { "url": str, "title": str|None, "text": str,            # main content, boilerplate stripped (trafilatura)
#   "anchors": [{"text": str, "selector": str|None}],       # headings/links usable as source_anchor
#   "chunks": [str] }                                        # text split for the LLM (deterministic)
```
- trafilatura main-content extraction; Docling path only when `PREFER_DOCLING` is truthy.
- Pure + deterministic over input HTML. Must not execute scripts or follow links. Injection markup in
  the HTML is treated as plain text (it is DATA, never instructions).

## 2. LabExtraction — `extraction/lab_schema.py` (pure)
The strict JSON schema the LLM must satisfy + the dataclass + validator.
```
@dataclass(frozen=True)
class LabExtraction:
    lab_name: str
    pi: str | None
    members: tuple[str, ...]          # member names AS WRITTEN on the page
    research_areas: tuple[str, ...]
    self_description: str | None      # extracted verbatim-ish from the page (NOT invented)
    source_anchor: str | None         # a heading/selector/snippet that grounds the extraction
    confidence: float                 # 0..1, the model's own confidence

LAB_JSON_SCHEMA: dict                 # JSON-schema for OpenRouter response_format
def validate(obj: dict) -> LabExtraction | None   # None if off-schema / missing required fields
```
- `validate` rejects anything not matching the schema (wrong types, extra control fields, missing
  required keys) → returns None → caller quarantines. This is the injection backstop: the model's
  output is data, validated structurally before it can affect the graph.

## 3. build_lab_rows — `discovery/build_lab_rows.py` (pure)
```
def build_lab_rows(extractions, institution, researcher_set, legacy_labs, run_key, source_keys,
                   *, min_confidence=0.5) -> dict
# returns {"accepted": ImportRows, "quarantined": [{"kind","payload","reason"}]}
```
Inputs:
- `extractions`: list of `{"extraction": LabExtraction, "source_url": str, "raw_key": str, "anchor": str|None}`.
- `institution`: `{"id": openalex_inst_id, "ror": str|None, "name": str}`.
- `researcher_set`: list of `{"id","name","normalized_name","openalex_id"}` (from the in-batch repop run).
- `legacy_labs`: list of `(lab_id, display_name)` (graph_core.LAB_LIST) for id-merge.

Rules:
- **Lab id / legacy merge:** if `normalize(lab_name)` matches a legacy display name → reuse that `lab_id`
  (so `MEMBER_OF` edges target the canonical lab and no duplicate lab node is created); else id =
  `lab:{inst}:{normalize(lab_name)}`. Dedupe labs by `normalize(name) + parent dept`.
  **Legacy lab nodes are immutable:** the loader upserts nodes `ON CONFLICT(id) DO NOTHING`, so a scraped
  lab matching a legacy id does NOT overwrite that node's name/attributes. This is deliberate — an
  *unpublished* scrape run must not mutate the published graph (snapshot isolation). Scraped enrichment
  (description/faculty/areas) therefore lands only on NEW (non-legacy) lab nodes; enriching an existing
  legacy lab is a future deliberate "promote" step, never an automatic side effect of a scrape run.
- **Member reconciliation:** `normalize(member)` matched against `researcher_set.normalized_name`.
  unique match → `MEMBER_OF` (researcher → lab, weight 1.0, confidence=extraction.confidence). No match →
  quarantine `{"kind":"member","payload":{member, lab}, "reason":"unmatched-researcher"}` (no edge).
- **Grounding / "no evidence → no claim":** a lab with `confidence < min_confidence` OR no `source_anchor`
  → quarantine the whole lab (kind "lab", reason) — it does NOT enter `accepted`.
- **Nodes:** lab (`val` 2; attributes `{description: self_description, faculty: [researcher_ids], url,
  research_areas, pi}`; `description_model:"scrape"`; `confidence`); department (`val` 6) when a parent dept
  is known. Every node/edge carries a `source_record_key` → one `source_record` per page (source='scrape',
  source_url, raw_s3_key=raw_key, evidence=anchor).
- **Edges:** `MEMBER_OF` researcher→lab; `PART_OF` lab→department and department→institution; `FOCUSES_ON`
  lab→topic only when a research_area maps to a known topic id (else skip — don't invent topic nodes).
- Pure: no HTTP/DB/clock/LLM. Deterministic + idempotent (stable lab ids; edge identity (src,dst,type)).

## Serving note
Lab nodes still serialize to the 4-field graph shape (`serialize_graph` unchanged) — the enriched
attributes (description/faculty) are exposed separately (a `/api/lab/{id}` read), so the existing graph
render is untouched.
