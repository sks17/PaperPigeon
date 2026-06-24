# Infrastructure, Security & Roadmap

*Part 4 of 4 — see also: Product Overview, API Information, Agent Structure.*

The remaining cross-cutting concerns: where it runs, how it stays safe, the auxiliary surfaces, and the order to build in.

---

## System architecture (CloudFlare + AWS + GitHub CI/CD)

You deferred the call, so here it is. Postgres-first, graph-DB-later, native orchestration, no n8n.

| Layer | Where | What |
|---|---|---|
| **Frontend + edge** | CloudFlare | Pages hosts the existing graph UI; CloudFlare in front for CDN/WAF/DDoS. Optional Workers for edge auth/caching. R2 is a viable cheaper object store if you want to dodge S3 egress. |
| **API** | AWS ECS Fargate (container, FastAPI or Node) behind an ALB | Long RAG calls and streaming live more comfortably on Fargate than Lambda. Thin read endpoints can be Lambda + API Gateway if you prefer; don't split prematurely. |
| **Repopulation workers** | AWS Fargate tasks pulled off **SQS** | One queue, idempotent workers. The scraper runs as a *separate* worker pool (different rate-limit profile — see API Information). Promote to **Step Functions** only when branching outgrows a queue. |
| **Database** | AWS RDS Postgres + **pgvector** | Nodes, typed edges, provenance, embeddings, relevance — all in one place. |
| **Raw store** | S3 (or R2) | Raw API responses + scraped HTML for replayability and provenance. |
| **Secrets** | AWS Secrets Manager (deployed) / gitignored `.env` (local) | See Security below. |
| **Graph DB** | *Later:* AWS Neptune or Neo4j | Only after traversal queries actually hurt in Postgres. |
| **CI/CD** | GitHub Actions | Build → ECR → Fargate; frontend → CloudFlare Pages; run migrations; lint/test; **secret-scan (gitleaks) + dependency audit** gates. |

**Why no n8n:** every reason it's attractive (visual scheduling, retries) is reproduced by SQS + a cron rule + idempotent workers, and unlike n8n that logic is in version control, unit-testable, reviewable by your coding agents, and deployable through the same pipeline as everything else.

---

## Security

**Secrets handling — currently fine, keep it that way.** OpenRouter, Gemini, and `OPENALEX_API_KEY` live locally in a gitignored `.env` and are *not* committed — that's exactly right. The standing rules: CI reads them from GitHub encrypted secrets; production reads from AWS Secrets Manager and injects at runtime; and **gitleaks runs as a CI gate** so a key can never land in git history by accident later. Don't widen this — local `.env` for dev, managed secret store for everything deployed.

**Scraped HTML is untrusted input.** It can contain prompt-injection payloads aimed at your extraction LLM. The AI extraction step must be a *pure data transform* — never give a tool-enabled / write-capable agent the raw scraped text, and never let the model's output trigger an action. Sanitize, extract to schema, validate, then act in your own code.

**SSRF.** If users submit institution URLs or lab-page seeds, validate the domain against the scraper allowlist, block private IP ranges and cloud metadata endpoints (`169.254.169.254`), and only fetch over HTTPS.

**Real-person data is personal data.** Ground every description in public professional sources, store the minimum needed, expose a correction/removal path, and don't fabricate. This is both ethics and liability.

**Least privilege everywhere.** Scoped IAM role per service (the API doesn't need the worker's S3 write scope, etc.); authenticated API; rate limiting; signed webhooks if you have any. Respect `robots.txt` and rate-limit scraping with an identifying user-agent.

**If you add MCP (below),** the same trust boundary applies: expose only safe, scoped tools, and remember MCP servers can carry injection risk via returned content.

---

## Auxiliary surfaces (nice-to-have, build after core)

- **MCP server over the graph** — expose a *few* safe, scoped tools so Claude Code / a desktop client can query and operate the graph: `search_labs`, `get_researcher`, `resolve_institution`, `get_provenance`, and a *gated* `trigger_repopulation`. Read-mostly; writes behind explicit confirmation. Connect from Claude Code with `claude mcp add --transport http ...`.
- **Desktop client** — Tauri (lighter than Electron) wrapping the existing frontend, or a thin client against the API.
- **Other interface "ports"** — a CLI, a browser extension, or a public read-only API are all reasonable later surfaces. None should precede a working core graph + repopulation + descriptions.

---

## Build sequence

1. **Foundation.** Schema + backend skeleton + RDS Postgres/pgvector on AWS; GitHub Actions CI/CD with secret-scanning (gitleaks); secrets in Secrets Manager for deployed envs, gitignored `.env` for local. Point the *existing* frontend at the new API so the current lab/researcher graph renders off the new backend. Nothing user-visible changes yet — that's the goal.
2. **Repopulation v1.** OpenAlex + ROR researcher discovery, on-demand SQS job, idempotent upsert with provenance, embeddings + query-scoped relevance.
3. **Lab layer.** Scraping + grounded AI extraction of labs and membership; the rich typed/weighted edge set.
4. **AI descriptions.** RAG-grounded per-node descriptions (OpenRouter + pgvector) for all node types; preserve existing features.
5. **Auxiliary.** MCP server, desktop client, extra interfaces.
6. **Scale/harden.** Step Functions if the pipeline branches enough to need it; Neptune/Neo4j if traversal queries hurt; observability dashboards over runs, counts, failures, and quarantine.

---

## Verify before you commit
- **API terms** — confirm OpenAlex **enterprise** pricing/limits before upgrading past the ~$1/day free cap; Crossref polite-pool behavior; and especially the **Semantic Scholar license** (full detail in API Information).
- **Scraping targets** — each department/lab site's `robots.txt` and ToS.
- **Existing service internals** — adapt this architecture to what's already there (current relevance formula, DB, frontend framework). The whole thing is meant to be **strictly additive**: the existing graph keeps working at every milestone.
