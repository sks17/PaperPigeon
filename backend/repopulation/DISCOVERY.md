# Discovery → ImportRows Contract (Phase 2)

How live OpenAlex/ROR data becomes the SAME `ImportRows` shape the loader already ingests
(`SCHEMA.md` §1–2). The pure transforms (`discovery/build_rows.py`, `relevance/score.py`) bind to
this doc; the main thread wires the live clients that feed them parsed dataclasses.

## Inputs (already parsed — pure dataclasses, no HTTP)
- `institution: RorOrganization` (from `ror_parse.parse_ror_organization`) — the resolved seed org.
- `authors: tuple[OpenAlexAuthor, ...]` (from `openalex_parse.parse_openalex_authors`) — each carries
  `id, orcid, display_name, last_known_institution, topics, recent_works, works_count,
  cited_by_count, h_index, i10_index`.
- `seed: {"institution": str, "topic": str | None, "keywords": [str], "openalex_institution_id": str}`.
- `run_key: str`, `source_keys: {"openalex": str, "ror": str}` (provenance keys → source_record_rows).

## Node `val` convention (extends the frontend 1=researcher / 2=lab)
`researcher=1, lab=2, institution=3, topic=4, paper=5, department=6, venue=7`. NOTE: the current
serializer (`graph_data.serialize_graph`) renders only `researcher`/`lab` nodes and
`paper`/`advisor`/`researcher_lab` links — so a served run snapshot shows the **researcher
subgraph** (researchers + coauthorship). `institution`/`topic`/`paper` nodes and AFFILIATED_WITH/
AUTHORED/WORKS_ON edges are **stored** (provenance + relevance inputs) but not yet rendered;
surfacing them in the UI is deferred frontend work. This keeps the existing frontend unchanged.

## Node mapping (dedup keys filled — order ORCID→OpenAlex→ROR→normalized name)
| Node | id | kind/val | key fields | attributes |
|------|----|----|----|----|
| Institution | openalex inst id | institution/3 | `ror`, `openalex_id`, `normalized_name` | `{country}` |
| Researcher | openalex author id | researcher/1 | `orcid`, `openalex_id`, `normalized_name` | `{papers:[{title,year,document_id,tags}], tags:[topic labels], h_index, works_count}` |
| Topic | openalex topic id | topic/4 | `openalex_id` | `{field, subfield, domain}` |
| Paper | openalex work id | paper/5 | `openalex_id`, `doi` | `{year, cited_by_count}` |

- `name`: institution canonical name / author display_name / topic display_name / work title.
- `ai_description = None` in Phase 2 (RAG descriptions are Phase 4). `confidence`: 1.0 for API-sourced.

## Edge mapping (every edge typed, weighted, provenance-bearing)
| Edge | src → dst | weight | from |
|------|----------|--------|------|
| `AFFILIATED_WITH` | researcher → institution | 1.0 | author.last_known_institution == seed inst |
| `AUTHORED` | researcher → paper | 1.0 | author.recent_works |
| `WORKS_ON` | researcher → topic | topic.score (share) | author.topics |
| `COAUTHORED_WITH` | researcher → researcher | #joint works | pairs co-occurring on the same work (within the discovered author set); directed by author-id order, symmetric-normalization deferred |
| `CITES` | paper → paper | 1.0 | optional — only if referenced works are present (parser doesn't expose them yet → skip in v1) |

- Each node/edge → a `source_record_key` ('openalex' or 'ror'); `raw_s3_key` set by the client.
- Idempotent: node `id` and edge `(src,dst,type)` are stable identity keys (loader upserts).

## `build_rows` signature (pure)
```
def build_import_rows(institution, authors, seed, run_key, source_keys) -> dict   # ImportRows (SCHEMA.md §1)
```
Returns `runs` (one, status 'running'→caller finalizes), `source_records` (openalex + ror),
`nodes`, `edges`, `relevance: []` (relevance is computed separately, post-embedding).

## Relevance (pure) — `relevance/score.py`
```
def score_relevance(seed_embedding, node_vectors, node_meta, run_key, weights=DEFAULT_WEIGHTS) -> list[relevance_row]
```
- `relevance = w1·cosine(seed_embedding, node_embedding) + w2·recency_decay(last_active_year)
  + w3·log1p(output_or_citation_volume)`.
- `node_vectors: {node_id: list[float]}`, `node_meta: {node_id: {last_year, volume}}`.
- `recency_decay(y) = exp(-(CURRENT_YEAR - y)/HALFLIFE)` (pass CURRENT_YEAR in — no wall-clock in pure code).
- Returns `relevance_row`s (SCHEMA.md §2) scoped to `run_key`, with `components` populated for explainability.
- Query-scoped: stored per (node, run). Caller embeds, supplies vectors, persists to `repop.relevance`.
