# Backend Cutover Runbook — legacy Flask → FastAPI + Postgres (fly.io)

Unblocks the Phase-4 frontend surfacing (grounded descriptions, lab detail, run snapshots) by
deploying the new **Repopulation API** (`backend/repopulation/api.py`) on a managed Postgres+pgvector
and routing the new endpoints to it. **Additive and reversible** — the legacy Vercel/Flask app keeps
serving the existing graph + AWS-backed features until you flip routing, and can be flipped back.

## What runs where (after cutover)

| Path | Served by | Notes |
|------|-----------|-------|
| `GET /api/graph/data` (+ `?run=`) | **fly FastAPI** | from Postgres; reproduces the legacy graph |
| `GET /api/node/description` | **fly FastAPI** | Phase-4 grounded description + evidence |
| `GET /api/lab` | **fly FastAPI** | Phase-4 enriched lab record |
| `POST /api/discover` (+ `GET /api/discover/{id}`) | **fly FastAPI** | key-gated on-demand ingestion of any ecosystem; enqueues a `discovery_job` |
| discovery worker | **fly worker process** (`[processes].worker`, always-on) | drains the job queue → runs the ingestion pipeline. Set `DISCOVERY_API_KEY` + see DEPLOY.md §5b |
| `POST /api/graph/paper-lab-id` | **Vercel/Flask** | DynamoDB lookup — not ported |
| `POST /api/rag/chat` | **Vercel/Flask** | Bedrock RAG — not ported |
| `POST /api/recommendations/from-resume` | **Vercel/Flask** | Bedrock — not ported |
| `POST /api/pdf/url` | **Vercel/Flask** | S3 presign — not ported |
| static frontend | **Vercel** | unchanged |

The AWS-backed endpoints are deliberately **not** moved (porting Bedrock/S3 is a later pass). The
frontend keeps calling them relative (Vercel/Flask); only the graph/description/lab calls honor the
new origin. See `src/services/dynamodb.ts` (`API_BASE`; `fetchPaperLabId` stays relative on purpose).

## Codified in this repo (no cloud needed)
- `Dockerfile` + `.dockerignore` — the API image (uvicorn `backend.repopulation.api:app`).
- `fly.toml` — fly service: `release_command` runs migrations+seed; `/health` check; scale-to-zero.
- `scripts/prod_migrate_seed.py` — idempotent: applies `migrations/*.sql` + seeds the legacy graph.
- `.github/workflows/ci.yml` — `backend-tests` now runs the full pytest suite (was stubbed).
- `src/services/dynamodb.ts` — `VITE_API_BASE_URL` + `fetchNodeDescription` / `fetchLabDetail` /
  `fetchGraphData(runId)`.

## Manual steps (a human with a fly.io account runs these)

> Prereqs: `flyctl` installed + `fly auth login`. From the repo root.

```bash
# 1. Create the app (uses fly.toml; edit `app =` first if you want a different name).
fly apps create paper-pigeon-api          # or: fly launch --no-deploy --copy-config

# 2. Managed Postgres 16 (ships with pgvector) + attach it (sets the DATABASE_URL secret).
fly postgres create --name paper-pigeon-db --region sea
fly postgres attach paper-pigeon-db --app paper-pigeon-api

# 3. Secrets (only needed if you run repopulate/describe/scrape jobs from this app; the read API
#    itself needs only DATABASE_URL). NEVER commit these.
fly secrets set --app paper-pigeon-api \
  OPENALEX_API_KEY=… OPENROUTER_API_KEY=… PAPERPIGEON_BUDGET_PRO_DAILY_USD=10

# 4. Deploy — builds the Dockerfile, runs release_command (migrate + seed), starts the service.
fly deploy --app paper-pigeon-api

# 5. Verify the new backend directly.
curl https://paper-pigeon-api.fly.dev/health                 # {"status":"ok"}
curl https://paper-pigeon-api.fly.dev/api/graph/data | jq '.nodes|length'   # 323
```

### Route the frontend's new-endpoint traffic to fly

Pick ONE:

- **Vercel path rewrites (recommended — keeps frontend calls relative).** In `vercel.json`, add
  rewrites for the moved paths to the fly origin *above* the catch-all, leaving the rest on Flask:
  ```json
  { "source": "/api/graph/data",        "destination": "https://paper-pigeon-api.fly.dev/api/graph/data" },
  { "source": "/api/node/description",  "destination": "https://paper-pigeon-api.fly.dev/api/node/description" },
  { "source": "/api/lab",               "destination": "https://paper-pigeon-api.fly.dev/api/lab" },
  ```
  (Keep the existing `/api/(.*) → /api/index.py` rewrite last so paper-lab-id/rag/recs/pdf stay on
  Flask.) Redeploy the frontend on Vercel.

- **Build-time origin.** Set `VITE_API_BASE_URL=https://paper-pigeon-api.fly.dev` in the Vercel
  project env and rebuild. The graph/description/lab calls go straight to fly; `fetchPaperLabId`
  stays relative (Flask). Note: CORS on the API is already `*`.

## Verify end-to-end
- Graph renders as before (323 nodes / 1043 links) off the new backend.
- `GET /api/node/description?id=<researcher>` returns a grounded `about` + evidence.
- RAG chat / recommendations / PDF links still work (still on Flask).

## Rollback
- **Vercel rewrites:** remove the three fly rewrites → all `/api/*` returns to Flask. Redeploy.
- **VITE_API_BASE_URL:** unset it → calls go relative again. Rebuild.
- The fly app + Postgres can stay running (idle, scaled to zero) or `fly apps destroy paper-pigeon-api`.

## Deferred next (post-cutover)
- Port the AWS-backed endpoints (paper-lab-id, rag, recommendations, pdf) onto the new backend, then
  retire the Flask function and route ALL `/api/*` to fly.
- Build the description/lab/run-snapshot UI on the new `fetch*` client functions.
