# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## ⚠️ Status: Major rework incoming

This repo is **about to undergo massive changes in both logic and infrastructure.** The architecture documented below describes the *current* (pre-rework) system. Treat it as the starting state, not a fixed contract:

- The cache-first / static-JSON data plane, the Vercel Python-serverless deployment, and the AWS service layer (DynamoDB / S3 / Bedrock) are all candidates to be replaced or restructured.
- The many `.md` design docs (`README.md`, `ARCHITECTURE_ANALYSIS.md`, `SETUP.md`, `docs/PROJECT_CONTEXT.md`, `FRONTEND_GRAPH_SCHEMA.md`) describe the current state and **will drift** — verify against actual code before relying on them, and update them when you change behavior.
- Don't assume an interface is load-bearing just because it exists. Confirm what the rework actually needs before extending current patterns.

## Commands

Frontend (Node / pnpm — `pnpm` is the canonical package manager; a `package-lock.json` also exists):

```bash
pnpm install          # install deps
pnpm dev              # Vite dev server at http://localhost:5173
pnpm build            # tsc -b (typecheck) + vite build  →  dist/   (this is the typecheck gate)
pnpm lint             # eslint .
pnpm preview          # serve production build locally
```

There is **no frontend unit-test runner configured** — `pnpm build` (which runs `tsc -b`) is the type/correctness gate. The Playwright E2E suite referenced in the README lives in a private repo and is not present here.

Backend (Python 3.12, Flask):

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt                   # flask, flask-cors, boto3, python-dotenv
python backend/app.py                             # Flask dev server at http://localhost:5000 (debug=True)
```

Graph cache / data tooling (all require AWS DynamoDB read creds; **run from project root**):

```bash
python backend/build_graph_cache.py        # build graph → backend/cache/graph_cache.json
python backend/precompute_graph.py         # build graph → upload to S3 (key "graph.json")
python backend/tools/rebuild_graph_cache.py
python tools/upload_cache.py               # upload backend/cache/graph_cache.json to S3
```

Deploy: `vercel --prod`, or push to the connected GitHub repo (Vercel auto-builds).

## Big-picture architecture

**Two apps, one repo:** a React/TypeScript SPA (`src/`) and a Flask API (`backend/`), wired together by Vercel. The product visualizes the UW Allen School research network as an interactive 3D force-directed graph (`/`) with an optional A-Frame VR view (`/vr`).

**Request flow:** the browser loads the SPA; all `/api/*` calls are rewritten by `vercel.json` to `api/index.py`, which imports the Flask `app` from `backend/app.py`. There are three thin Flask entry points that all re-export the same app — `api/index.py`, `api/app.py`, and `backend_wrapper.py` — kept for different deployment import paths. The real app and routes live in `backend/app.py`.

**Cache-first data plane (the key design choice).** Graph reads do **not** hit DynamoDB at request time. Instead:
- The full graph is precomputed into a static `graph_cache.json` and `GET /api/graph/data` serves it from an in-memory copy.
- `backend/app.py:load_graph_cache()` lazy-loads on first request, trying several paths in order: `backend/cache/`, `public/`, `dist/`, then Vercel's `/var/task/...` absolute paths. Missing cache → empty `{nodes:[], links:[]}` (graph silently renders empty rather than erroring).
- **There are two copies of the cache**: `public/graph_cache.json` (shipped with the frontend bundle and included in the serverless function via `vercel.json` `includeFiles`) and `backend/cache/graph_cache.json` (local/dev). Keep them in mind when data looks stale.
- Cache *writing* is disabled in the deployed app: `POST /api/graph/rebuild-cache` returns 503 because Vercel's filesystem is read-only. The cache is rebuilt out-of-band by the scripts above (cron lives in a private repo).

**Where DynamoDB is still hit at runtime:** only the non-graph endpoints — `paper-lab-id`, and the AWS-backed controllers below.

**Backend layering** (`backend/`):
- `graph_core.py` — pure graph builder; assembles nodes/links from DynamoDB to **exactly** match the frontend TS schema. Contains the hardcoded `LAB_LIST` of `lab_id → display name`; this list must stay in sync with the frontend's understanding of labs.
- `controllers/` — Flask blueprints, registered under `/api/rag`, `/api/recommendations`, `/api/pdf`.
- `services/` — AWS wrappers (`dynamodb_service.py`, `s3_service.py`, `bedrock_service.py`). All use **lazy** boto3 client init (avoids serverless cold-start cost) and in-memory caching. Each reads env vars via a "first non-empty of N names" helper, so several env var aliases may resolve the same setting.

**Frontend** (`src/`): React 19 + Vite 7 + Tailwind v4 + shadcn/ui (`src/components/ui/`). `App.tsx` wraps everything in `AccessibilityProvider` + `BrowserRouter` and fetches graph data on mount. `src/services/dynamodb.ts` is the API client (relative URLs only — works on any domain) and the **source of truth for the `GraphData`/`Node`/`Link`/`Paper` TypeScript types**. 3D rendering is `3d-force-graph` + three.js (`ResearchNetworkGraph.tsx`); VR is `3d-force-graph-vr` + A-Frame (`VRGraph.tsx`).

**The graph schema is a cross-language contract.** Python (`graph_core.py`) produces JSON that must satisfy the TypeScript interfaces in `src/services/dynamodb.ts`. Node convention: `val: 1` = researcher (sphere), `val: 2` = lab (box); links have `type` ∈ `paper | advisor | researcher_lab`. When you change one side, change both. `FRONTEND_GRAPH_SCHEMA.md` documents this contract in detail.

## API endpoints (backend/app.py + blueprints)

| Endpoint | Method | Notes |
|----------|--------|-------|
| `/api/graph/data` | GET | Serves in-memory cache; no DynamoDB |
| `/api/graph/rebuild-cache` | POST | **Disabled** — returns 503 on Vercel |
| `/api/graph/paper-lab-id` | POST | DynamoDB lookup: `document_id` → `lab_id` |
| `/api/rag/chat` | POST | Bedrock RAG over a paper (primary KB) |
| `/api/recommendations/from-resume` | POST | Bedrock recs (secondary KB); frontend has a Jaccard-overlap fallback if this fails |
| `/api/pdf/url` | POST | S3 presigned URL, key pattern `{lab_id}/{document_id}.pdf`, 1h expiry |
| `/health` | GET | `{status: ok}` |

## Environment variables (backend, via `os.getenv`)

`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_NAME`, `BEDROCK_KNOWLEDGE_BASE_ID` (paper chat), `BEDROCK_KNOWLEDGE_BASE_ID_2` (recommendations). The frontend needs **no** env vars. `NODE_ENV != 'production'` toggles verbose RAG/diagnostic logging in the services. Local builders/scripts load `.env` via `python-dotenv`.

## Gotchas

- **Run Python scripts from the project root**, not from `backend/` — imports are package-style (`from backend.services... import ...`).
- Without AWS creds locally, the graph still loads from `public/graph_cache.json`, but RAG chat, recommendations, and PDF links will fail — this is expected.
- `local-dev-test/` is a standalone HTML/JS harness for the force graph, independent of the React app.
- CORS is open to `*` (Vercel is relied on for domain security).
