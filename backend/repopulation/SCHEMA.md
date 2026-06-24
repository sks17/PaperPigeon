# Repopulation Engine — Phase 1 Data Contract

Single source of truth for the **intermediate row-dict shapes** that the importer produces and
the serializer consumes, plus the **exact frontend output contract**. The SQL schema lives in
`migrations/0001_initial.sql`; this doc is the contract the pure-function Cursor tasks (P1-T02,
P1-T03) implement against so their outputs interlock. **If these disagree, the round-trip golden
test (P1-T05) fails.**

> Design goal: `serialize_graph(*cache_to_rows(load(graph_cache.json)))` reproduces the original
> graph **structurally** — same node order, same link order, same fields/values. JSON *key*
> order is irrelevant (compare as parsed objects, not bytes).

---

## 1. Pure functions (no DB, no I/O, no network)

```
# importer/cache_to_rows.py  (P1-T03)
def cache_to_rows(graph: dict) -> dict:
    """graph = the parsed graph_cache.json ({"nodes":[...], "links":[...]}).
    Returns ImportRows (below). MUST preserve input order of nodes and links.
    MUST be idempotent at the data level: identical input → identical output, and the
    row identity keys (node 'id'; edge (src_id,dst_id,type)) never collide for distinct inputs."""

# serializers/graph_data.py  (P1-T02)
def serialize_graph(nodes: list[dict], edges: list[dict],
                    relevance_by_node: dict[str, float]) -> dict:
    """Inverse of the import for the rendered subset. Returns {"nodes":[...], "links":[...]}
    matching the frontend contract in section 4. MUST preserve the order of `nodes` and `edges`."""
```

`ImportRows` (return of `cache_to_rows`):
```
{
  "runs":           [run_row],            # exactly one: the legacy run
  "source_records": [source_record_row],  # exactly one: the legacy_cache record
  "nodes":          [node_row, ...],      # in original cache node order
  "edges":          [edge_row, ...],      # in original cache link order
  "relevance":      [relevance_row, ...], # one per researcher whose influence is not None
}
```

---

## 2. Row-dict shapes (mirror the SQL columns)

**node_row**
```
{ "id": str, "kind": "researcher"|"lab"|...,        # Phase 1 imports only researcher|lab
  "name": str, "val": int,                          # 1 researcher, 2 lab
  "orcid": str|None, "openalex_id": str|None, "ror": str|None, "normalized_name": str|None,
  "attributes": dict,                               # see below — type-specific rendered fields
  "ai_description": str|None,                        # researcher 'about' (None if "" / absent)
  "description_model": str|None,                     # "legacy_dynamodb" when ai_description set
  "description_generated_at": str|None, "description_evidence": list|None,
  "confidence": float|None,
  "source_record_key": str }                         # logical FK → source_record_row.key
```
- Researcher `attributes` = `{ "advisor": str|None, "contact_info": list[str], "labs": list[str],
  "standing": str|None, "papers": list[{title,year,document_id,tags}], "tags": list[str] }`.
- Lab `attributes` = `{}`.

**edge_row**
```
{ "src_id": str, "dst_id": str, "type": EDGE_TYPE,   # rich type, see section 3
  "weight": float, "directed": bool, "attributes": dict,
  "confidence": float|None, "source_record_key": str }
```

**source_record_row**: `{ "key": "legacy", "source": "legacy_cache", "source_url": None,
"retrieved_at": None, "confidence": None, "evidence": "imported from public/graph_cache.json",
"run_key": "legacy", "raw_s3_key": None }`

**run_row**: `{ "key": "legacy", "seed": {"source": "legacy_cache"}, "status": "succeeded" }`

**relevance_row**: `{ "node_id": str, "run_key": "legacy", "score": float, "components": None }`

---

## 3. Edge type mapping (rich ↔ rendered) — a bijection for the 3 legacy types

| Frontend `link.type` (cache) | Stored `edge.type` (rich) | Direction (src → dst) |
|------------------------------|---------------------------|-----------------------|
| `paper`                      | `COAUTHORED_WITH`         | researcher → researcher |
| `advisor`                    | `ADVISED_BY`              | advisee → advisor |
| `researcher_lab`             | `MEMBER_OF`               | researcher → lab |

- **Importer** applies the forward map (cache → rich). **Serializer** applies the reverse map
  (rich → cache), emitting ONLY edges whose rich type is in this table; any other rich type is
  skipped (not yet rendered). Legacy import sets `weight: 1.0`, `directed: true`.

### Parallel `paper` edges → weighted `COAUTHORED_WITH` (DB-load strategy)
The legacy cache stores **parallel `paper` links** — one per co-authored paper (verified: 633
paper links collapse to 322 unique pairs, max multiplicity 9 = #joint works). advisor/
researcher_lab are already unique. To honor both the product model (*"COAUTHORED_WITH, weight =
#joint works"*) and the DB's `UNIQUE(src_id,dst_id,type)` idempotency constraint, the two
representations differ **by design**:
- **Pure importer/serializer (this contract, Phase-1 frontend reproduction):** edges stay **1:1**
  with the cache — parallel paper edges preserved — so `serialize(cache_to_rows(cache)) == cache`
  exactly. (Edge identities are intentionally NOT unique here.)
- **DB-load path (main-thread code, run against Postgres):** collapses each parallel paper set
  into ONE `COAUTHORED_WITH` row with `weight = count`; the **DB-backed** `/api/graph/data`
  serializer **expands** by `weight` to re-emit the parallel `paper` links — reproducing all 633
  paper links (and thus all 1043 links across the three types; advisor/researcher_lab are 1:1).
  *(Symmetric-pair normalization — treating (A,B) and (B,A) as one — is a Phase-2 refinement.)*

---

## 4. Frontend output contract (what `serialize_graph` MUST emit)

Authoritative shapes are the TS interfaces in `src/services/dynamodb.ts` and the live emitter
`backend/graph_core.py`. Verified against `public/graph_cache.json`: 298 researchers, 25 labs,
links paper=633/advisor=190/researcher_lab=220.

**Researcher node** — emit ALL 12 keys every time, `null` where the value is absent:
```
{ "id", "name", "type": "researcher", "val": 1,
  "advisor", "contact_info", "labs", "standing", "papers", "tags", "influence", "about" }
```
- `advisor/contact_info/labs/standing/papers/tags` ← `node_row.attributes`.
- `influence` ← `relevance_by_node.get(id)` (i.e. the legacy run's score), else `null`.
- `about` ← `node_row.ai_description`, else `null`.
- `papers[i]` = `{ "title", "year", "document_id", "tags" }`.

**Lab node** — exactly 4 keys:
```
{ "id", "name", "type": "lab", "val": 2 }
```

**Link**: `{ "source": src_id, "target": dst_id, "type": <rendered> }`.

Ordering: nodes in input order (researchers then labs, as the cache has them); links in input
order. This is what makes the round-trip exact.
