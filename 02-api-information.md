# API Information

*Part 2 of 4 — see also: Product Overview, Agent Structure, Infrastructure/Security/Roadmap.*

Every external data source, its role, its wired endpoint, and how the scraper that fills the lab layer should work. **API integration is Claude main-thread work** (see Agent Structure) — Cursor and Copilot consume the internal clients Claude builds; they do not wire these APIs themselves.

---

## APIs you actually need

Ranked by role for *researcher/lab discovery*, not citation backbone. Verify current rate/pricing/license terms directly before committing — several below change over time, and at least one has a license trap.

| API | Role in your product | Endpoint & notes |
|---|---|---|
| **OpenAlex** | **Primary backbone.** Institution → authors, per-author topics/concepts, works counts, recent works, citation counts. Answers *who works at X* and *on what*. | Base `https://api.openalex.org`; API key is **mandatory since 2026-02-13** (the old `?mailto=` polite pool behavior no longer applies to OpenAlex), and keyless requests return 409 after ~100 credits. Docs moved from `docs.openalex.org` to `developers.openalex.org` (the former redirects). Pricing is usage-based credits: $0 single-entity lookups (unlimited) · $0.10/1k list+filter (10k/day) · $1/1k search (1k/day) · $10/1k content downloads (100/day); free key = $1 credit/day (about ~$1/day usage cap). Filter `authors` by `last_known_institutions.id` / `affiliations.institution.id`; filter `works` by institution + topic for the relevance signal. Abstracts are delivered as an inverted index format; OpenAlex data is CC0 and redistributable. The $1/day cap is a hard design constraint: cache aggressively and batch. |
| **ROR** | Canonical institution resolution ("UW" / "Allen School" → stable ID). | Base `https://api.ror.org` — health check / wired hook at `https://api.ror.org/heartbeat`. Free. Register a free client ID before Q3 2026 to retain 2,000 req/5min (otherwise it drops to 50/5min). This is your join key for the institution layer. |
| **arXiv** | Recent-preprint recall for CS/ML/physics. Best for current-work relevance and recovering output before DOI assignment. | Endpoint `https://export.arxiv.org/api/query` (Atom feed). Free. Slow: ~1 req / 3s, one connection at a time. Metadata is CC0; full-text rights vary. |
| **Crossref REST** | DOI metadata enrichment — affiliations, ROR IDs, abstracts where deposited, references. | Base `https://api.crossref.org`. Always use the **polite pool** by appending `?mailto=you@domain`. Post-2025-12-01 limits: polite pool 10 req/s for single-DOI and 3 req/s for lists (concurrency 3); public pool 5 req/s for single-DOI and 1 req/s for lists. Key endpoints: `/works`, `/works/{doi}`, `/works/{doi}/agency`, `/journals/{issn}/works`, `/members/{id}/works`, `/types/{id}/works`, `/funders/{id}/works`. Supports content negotiation for alternate formats. Secondary enrichment, not discovery. |
| **PubMed / E-utilities** | Biomedical-only supplement. Use only if your seeds touch medicine/life-sciences. | Base `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`; 3 req/s (10 with a key). Flow: **ESearch** (`esearch.fcgi` → PMIDs) → **ESummary** (`esummary.fcgi`, fast graph-population metadata: title, journal, pubdate, authors, IDs) or **EFetch** (`efetch.fcgi`, XML with abstracts + author `AffiliationInfo`), with **ELink** (`elink.fcgi`) for related / PMC full-text / citation-style links. Always send `tool` + `email` params; supports `retmode=json` on several endpoints. |
| **Semantic Scholar** | Rich author profiles (h-index, paper lists), good relevance signal. | **License trap:** default API terms are *non-commercial / internal research* only. Do **not** make it core unless your use clearly fits or you negotiate an expanded license. |

**Dropped:** **ORCID** (its public API is non-commercial, so it's out as a live dependency). Note the ORCID *identifier* still arrives inside OpenAlex author records, so dedup-by-ORCID and showing an ORCID link both survive — you just don't call the ORCID API for bios/education. Education and advising history therefore lean on scraping and stay low-confidence.

**Do not adopt as core:** Dimensions and Scopus (subscription, and both restrict derivative/dashboard products — only if your institution already pays and the terms fit). **Microsoft Academic Graph is dead** (retired after 2021); ignore it except for migrating legacy data.

### PubMed call pattern (reference)
```
ESearch:   {base}/esearch.fcgi?db=pubmed&term=<query>&retmode=json&retmax=N&tool=...&email=...
ESummary:  {base}/esummary.fcgi?db=pubmed&id=<csv-pmids>&retmode=json
EFetch:    {base}/efetch.fcgi?db=pubmed&id=<pmid>&retmode=xml   # abstracts + affiliations
ELink:     {base}/elink.fcgi?db=pubmed&id=<pmid>                # related / citation-style links
```
Field tags supported in `term`: `cancer[Title]`, `machine learning[Title/Abstract]`, `Smith J[Author]`, `Nature[Journal]`, `2024[Date - Publication]`, `clinical trial[Publication Type]`.

---

## The lab layer has no API

No scholarly API treats a *lab* as a durable first-class entity. Labs come from two sources only:

- **Targeted web scraping** of department faculty directories and individual lab websites — the real source of truth for which labs exist, who's in them, and the lab's self-description.
- **AI extraction** over scraped HTML + raw affiliation strings to produce lab nodes, membership edges, and descriptions.

So labs are *inferred* and must carry confidence + provenance.

---

## Web scraping subsystem (high level)

The scraper is the only place labs come from, so it deserves its own design. Treat it as a four-stage funnel, each stage narrower and more validated than the last, running as its **own SQS-fed Fargate worker pool** separate from the API-fetch workers (different rate-limit profile, different scaling).

**1. Discovery — find candidate pages.** From a resolved institution, walk: department index → faculty directory → individual faculty page → linked lab/group site. Seed URLs from OpenAlex author homepage fields where present, plus the domain's `sitemap.xml`. Keep a strict **same-domain allowlist** (the institution's and known department domains); never follow off-domain links during discovery. This is also your SSRF boundary (see Infrastructure/Security/Roadmap → Security).

**2. Fetch — polite, identifiable, cached.** Respect `robots.txt` and any crawl-delay. Send one identifying `User-Agent` with a contact URL. Enforce a **per-host token bucket** (rate limit + concurrency cap) so you never hammer a single university. Use conditional requests (`ETag` / `If-Modified-Since`) and a content hash so unchanged pages aren't reprocessed. Persist raw HTML + response headers + `fetched_at` to S3, keyed by URL hash, before anything else touches it. **Static-first:** try a plain HTTP fetch; escalate to a headless browser (Playwright on Fargate) only for JS-rendered pages, since it's 10–50× the cost.

**3. Extract — AI as a pure data transform.** Strip the raw HTML to readable text/DOM, chunk it, and send to the cheap model (Gemini Flash via OpenRouter) with a **strict JSON schema**: lab name, PI, members, research areas, self-description, and the source anchor/selector. Escalate to a stronger model only on low confidence. **The model output is data, never an action** — the extraction step has no tools and can trigger nothing, because scraped HTML is untrusted and may carry prompt-injection payloads.

**4. Validate + reconcile.** Cross-check extracted members against the OpenAlex researcher set already pulled for that institution; members that don't match get lower confidence. Dedupe labs by `normalized(name) + parent department`. Attach a `SourceRecord` (URL + selector + `fetched_at`) to every node and edge the scrape produced. Route low-confidence or conflicting records to quarantine, not the live graph.

Re-scrape on a **per-domain cadence**, not per repopulation run — the lab structure of a department changes monthly at most, so cache hits should be the common case and live fetches the exception.

---

## Verify before you commit
- **API terms** — confirm OpenAlex **enterprise** pricing/limits before you upgrade past the ~$1/day free cap; Crossref polite-pool behavior; and especially the **Semantic Scholar license** (don't build it into the core path until the license is settled).
- **Scraping targets** — each department/lab site's `robots.txt` and ToS before adding it to the allowlist.
