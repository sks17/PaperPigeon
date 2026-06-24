# Product Overview

*Part 1 of 4 — see also: API Information, Agent Structure, Infrastructure/Security/Roadmap.*

What you're building, the data it produces, the engine that fills it, and the AI features layered on top.

---

## What you're actually building (the correction)

Both source documents modeled this as a *citation graph of papers, keyed by institution, run by n8n*. Your product is different in three ways that change every downstream decision:

1. **The primary nodes are researchers and labs**, not papers. Papers are *evidence* that feed relevance scores and AI descriptions — they are not the object of the graph.
2. **Repopulation is query-conditioned and user-triggered.** The user says "rebuild around UW Allen School + computer vision" and the graph regenerates *for that scope*, with relevance computed *relative to that seed*. This is not a nightly cron over a fixed corpus.
3. **n8n is the wrong core.** It was the framing of both docs. For a product on CloudFlare + AWS with GitHub CI/CD, n8n becomes an untestable, un-versioned dependency holding business logic. Drop it from the product path. (Keep it only as an optional throwaway visual cron during prototyping, never as the place logic lives.)

**The service to build on top of the existing one** is a *Repopulation Engine*: a parameterized, idempotent, provenance-preserving pipeline that takes a seed (institution + optional topic/department/keywords), discovers researchers and labs from APIs + scraping, scores their relevance to the seed, builds a richly-typed edge set, generates grounded AI descriptions per node, and upserts a graph delta — all callable on demand from the existing UI.

---

## Data model

You asked specifically for **richer edges**. "Richer" means three things: more *types*, plus every edge carrying a *weight* and *provenance*.

### Nodes
`Researcher`, `Lab`, `Institution`, `Department`, `Topic`, `Venue`, `Paper`, and a `SourceRecord` node for provenance. Each substantive node also gets an `ai_description` + the metadata to regenerate it.

```
Researcher: id, name, orcid, openalex_id, h_index?, recent_topics[],
            ai_description, description_model, description_generated_at,
            relevance_score (query-scoped), embedding
Lab:        id, name, normalized_name, parent_department_id, verification_url,
            ai_description, confidence, embedding
Institution:id, ror, openalex_id, name, country
Paper:      id, doi, openalex_id, arxiv_id, title, year, citation_count
```

### Edges (the rich set)
Typed, directed, **weighted**, and provenance-bearing:

- `Researcher —AUTHORED→ Paper`
- `Researcher —MEMBER_OF→ Lab` (weight = membership confidence)
- `Researcher —AFFILIATED_WITH→ Institution`
- `Lab —PART_OF→ Department —PART_OF→ Institution`
- `Researcher —COAUTHORED_WITH→ Researcher` (weight = #joint works)
- `Researcher —ADVISES / ADVISED_BY→ Researcher` (PI ↔ student, from scraped lab/faculty pages)
- `Lab —COLLABORATES_WITH→ Lab` (derived from cross-lab coauthorship)
- `Researcher —WORKS_ON→ Topic` (weight = topic share)
- `Lab —FOCUSES_ON→ Topic`
- `Paper —CITES→ Paper`
- `Researcher —ALUMNUS_OF→ Institution` (scrape-derived, best-effort — ORCID was the natural source and is dropped, so treat as low-confidence)
- `* —SIMILAR_TO→ *` (embedding-derived; this is what powers recommendation/relevance traversal)
- `* —HAS_PROVENANCE→ SourceRecord`

### Provenance (on every node and edge)
```json
{
  "source": "openalex | crossref | arxiv | pubmed | scrape | ai",
  "source_url": "...",
  "retrieved_at": "...",
  "confidence": 0.0,
  "evidence": "affiliation string / API field / scraped selector",
  "repopulation_run_id": "..."
}
```

This is the spine of the whole product: it lets you debug bad data, detect injected/stale lab pages, and — critically — show users *why* a node or edge exists.

### Relevance score
Relevance is **relative to the active seed/query**, computed at repopulation time:

```
relevance = w1 · cosine(seed_embedding, node_embedding)
          + w2 · recency_decay(last_active_year)
          + w3 · log(output_or_citation_volume)
```

Store it scoped to the run/query, not as a global property — the same researcher is highly relevant to "computer vision" and irrelevant to "theory."

### Deduplication order (idempotency)
- **Researchers:** ORCID → OpenAlex author ID → normalized(name) + institution. *(The ORCID identifier still arrives inside OpenAlex author records even though the ORCID API is dropped — see API Information.)*
- **Institutions:** ROR → OpenAlex ID.
- **Labs:** normalized(name) + parent department.
- **Papers:** DOI → PMID/arXiv ID → source paper ID → normalized(title)+year+first-author.

---

## The Repopulation Engine (the service on top)

A parameterized, idempotent, replayable pipeline. Triggered by a seed; each stage writes raw payloads to S3 before transforming.

```
submit(seed) → SQS
  1. resolve institution         → ROR / OpenAlex ID
  2. discover researchers        → OpenAlex authors + topics + recent works
  3. enrich researchers          → arXiv (recent preprints) + Crossref (DOI metadata)
  4. scrape labs                 → dept directories + lab sites → raw HTML to S3
  5. AI extract labs + membership→ grounded LLM over scraped HTML + affiliations
  6. embed + score relevance     → pgvector; relevance vs seed_embedding
  7. build/refresh edges         → the rich typed/weighted set
  8. generate AI node descriptions → RAG-grounded (see AI features below)
  9. upsert graph delta          → with provenance + confidence
 10. publish snapshot            → notify frontend
```

> Stages 4–5 (scraping + AI extraction) are detailed in the **API Information** doc — that's where the data-acquisition design lives. The infrastructure for the queue/workers is in the **Infrastructure/Security/Roadmap** doc.

Properties to enforce, because they're what make it credible rather than a demo:

- **Idempotent** — re-running the same seed produces no duplicate nodes (dedupe order above).
- **Incremental** — only new/changed researchers and works are reprocessed.
- **Replayable** — raw API/scrape payloads stored in S3 before any transform.
- **Provenance-preserving** — every node and edge points back to a `SourceRecord`.
- **Quarantine, don't crash** — low-confidence or conflicting records route to a review state, not into the live graph.
- **Budget-aware** — the OpenAlex free plan's ~$1/day ceiling means a single large institution sweep can exhaust the daily quota. Cache every OpenAlex response in S3 + Postgres, prefer cheap summary calls before expensive ones, batch author/work lookups, and let a repopulation run resume across days against the cache rather than re-hitting the API.

---

## AI features (keep what exists, add per-node descriptions, production RAG)

**Keep:** the existing rich researcher descriptions and relevance scoring. Don't rebuild — extend.

**Add — AI description per node, for every node type** (researcher, lab, topic, institution). The hard requirement: **grounded generation, never free invention.** These describe real people; an ungrounded "description" is a hallucination and a defamation/accuracy risk. So each description is a small RAG task:

```
retrieve evidence (recent papers, scraped faculty/lab page, OpenAlex topics)
  → generate description constrained to that evidence
  → store: text + model + version + generated_at + evidence_pointers
  → cache; regenerate on demand
```

**Production RAG stack** ("OpenRouter alongside something else"):

- **OpenRouter** = the LLM + embedding **gateway** — model routing, fallback, and cost control across providers.
- **pgvector** = the retrieval store (keeps RAG in the DB you already run; no extra infra). Reach for a managed vector DB only if scale forces it.
- **Optional: LlamaIndex** as the retrieval/orchestration layer if you want batteries-included document-RAG ergonomics rather than hand-rolling chunking/retrieval.
- **Model tiering for cost:** Gemini Flash (or similar cheap model) for bulk extraction + descriptions; escalate to a stronger model *only on low confidence*. This is where your Gemini and OpenRouter keys earn their place.

Retrieval = vector search over (paper abstracts + bios + scraped descriptions) **filtered by graph context** (this researcher, this lab), so descriptions stay scoped and grounded.
