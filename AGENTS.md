# Paper Pigeon — Agent Command Center

Single source of truth for the build-time agent swarm. **Claude Code main thread reads this first.**
Cursor tasks are excerpts of it; Copilot follows its list; OpenClaw reports against its constraints.
Full detail lives in the four planning docs: `01-product-overview.md`, `02-api-information.md`,
`03-agent-structure.md`, `04-infrastructure-security-and-roadmap.md`.

## Mission
Build the **Repopulation Engine** — a strictly-additive layer over the existing lab↔researcher graph.
On a user seed (institution + optional topic/department/keywords) it discovers researchers and labs
from scholarly APIs + targeted scraping, builds typed/weighted/provenance-bearing edges, generates
grounded RAG descriptions for every node type, and upserts a graph delta — on demand from the existing UI.

**The existing graph must keep working at every step.** Additive only.

## Current architecture (starting state — verify against code, the docs drift)
- **Frontend** `src/` — React 19 + Vite 7 + Tailwind v4 + shadcn; 3D graph via `3d-force-graph`/three.js.
  `src/services/dynamodb.ts` is the TS source of truth for `GraphData`/`Node`/`Link`/`Paper`.
- **Backend** `backend/` — Flask; `graph_core.py` builds the graph to match the TS schema; cache-first
  data plane serves static `graph_cache.json`. AWS (DynamoDB/S3/Bedrock) behind lazy boto3 clients.
- **Schema is a cross-language contract**: Python output must satisfy the TS interfaces. `val:1`=researcher,
  `val:2`=lab; `link.type` ∈ `paper|advisor|researcher_lab` (extended **additively** for new edge types).

## Target runtime
Edge → API → queue workers (scraper is a separate pool) → Postgres + pgvector → object store (S3).
GitHub Actions CI/CD. Postgres-first; graph DB only if traversal later demands it. **No n8n.**
**Deploy target pivoted to fly.io** (was AWS Fargate); **deployment work is DEFERRED** — Phase 1
builds and tests against a **local Postgres** (Docker). Provisioning (Fly Postgres/managed PG, queue,
object store) and the production cutover happen in a later pass, not now.

## Non-negotiable constraints
1. **Additive, never breaking.** No change may alter what the existing graph renders.
2. **Secrets:** local gitignored `.env` only; deployed envs use the managed store. gitleaks is a CI gate.
   The `guard.py` PreToolUse hook blocks writes to `.env*`/keys/lock files.
3. **Scraped HTML is untrusted.** AI extraction is a pure data transform — no tools, output is data,
   never an action. SSRF allowlist + private-IP/metadata block on any URL fetch.
4. **Provenance on every node and edge.** Idempotent, replayable, quarantine-don't-crash.
5. **Grounded AI only.** Real-person descriptions must cite evidence; no free invention.
6. **Budget-aware** against OpenAlex's ~$1/day cap: cache + batch, resume across days.

## The ladder (who may do what)
| Tier | Who | Meta-level (plan / API integration / run commands)? |
|---|---|---|
| Orchestrator | **Claude main thread** | YES — exclusively. Holds merge authority. |
| Scouts/reviewers | **Claude subagents** (`.claude/agents/`) | No — read-mostly, structured memos. |
| Implementers | **Cursor** (≤10, looping) | No — scoped implementation only. |
| Cleanup | **OpenClaw** | No — read-only, emits cleanup reports. |
| Low-level | **Copilot** | No — sequential small items. |

- **Subagents:** repo-cartographer, data-source-researcher, schema-reviewer, security-reviewer,
  code-reviewer, test-strategist (defined in `.claude/agents/`).
- **Task pools:** Cursor → [`cursor-tasks.md`](./cursor-tasks.md) (parallel, file-disjoint).
  Copilot → [`copilot-tasks.md`](./copilot-tasks.md) (sequential checklist).
- **Anti-patterns to refuse:** agent pipelines (A→B→C), test-runner subagents, expert-persona agents,
  Cursor doing meta work, overlapping Cursor file scopes, OpenClaw/Copilot making structural changes.

## Build sequence (each milestone keeps the existing graph working)
1. **Foundation** — schema + backend skeleton + Postgres/pgvector (local Docker now; Fly later);
   import the existing graph into Postgres and serve it back structurally-identical; CI with
   gitleaks; nothing user-visible changes. *Deployment deferred.*
2. **Repopulation v1** — OpenAlex + ROR discovery; on-demand SQS job; idempotent provenance upsert;
   embeddings + query-scoped relevance.
3. **Lab layer** — scraping + grounded AI extraction; the rich typed/weighted edge set.
4. **AI descriptions** — RAG-grounded per-node descriptions (OpenRouter + pgvector); preserve existing.
5. **Auxiliary** — MCP server over the graph, desktop client, extra interfaces.
6. **Scale/harden** — Step Functions if branching demands; Neptune/Neo4j if traversal hurts; dashboards.

## Branch plan (map each Cursor task's `Files allowed` to one branch → merge-clean)
```
main
 ├── agent/backend-api
 ├── agent/repopulation-engine
 ├── agent/graph-schema
 ├── agent/scraping-and-ai-extraction
 ├── agent/ai-descriptions-rag
 ├── agent/security-hardening
 └── agent/infra-cicd
```

## Canonical commands (main thread runs these — never a subagent/Cursor/Copilot)
```
pnpm install            # frontend deps
pnpm lint               # eslint
pnpm build              # tsc -b (typecheck gate) + vite build
pnpm dev                # Vite dev server
python -m pytest -q     # backend tests (added per layer)
gitleaks detect         # local secret scan (also a CI gate)
```

## MCP servers (`.mcp.json`, team-shared)
context7 (live docs) · playwright (scraper headless + frontend E2E) · github (issues/PRs/Actions) ·
postgres (read-only role over Postgres+pgvector — local Docker now, Fly Postgres later).
Credentials come from `.env` — see `.env.example`.
