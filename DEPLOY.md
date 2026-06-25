# Paper Pigeon — Deployment Guide (click-by-click)

This is the precise, step-by-step guide to deploy Paper Pigeon to production. It assumes **no prior
fly.io / Vercel experience** and tells you exactly what to type and which buttons to click.

For *why* the architecture is split this way (and the rollback rationale), see [`CUTOVER.md`](./CUTOVER.md).

---

## 0. What you are deploying

Two pieces:

| Piece | Hosted on | What it serves |
|-------|-----------|----------------|
| **Frontend** (React/Vite SPA in `src/`) | **Vercel** | the website + the legacy AWS-backed API (`/api/rag`, `/api/pdf`, `/api/recommendations`, `/api/graph/paper-lab-id`) via the existing Flask function |
| **Repopulation API** (FastAPI in `backend/repopulation/`) | **fly.io** + Postgres | `/api/graph/data` (+ `?run=`), `/api/node/description`, `/api/lab`, `/api/runs` |

The frontend calls the new endpoints; Vercel rewrites route them to fly. Everything else stays on
Vercel/Flask. **Nothing here is destructive** — you can roll back at any step (Section 7).

```
            ┌────────────────────────── Vercel ──────────────────────────┐
browser ──► │  React SPA  +  Flask fn (rag / pdf / recommendations)        │
            │      │                                                       │
            │      │  /api/graph/data, /api/node/description, /api/lab,     │
            │      ▼  /api/runs   ── rewrite ──►  ┌──────── fly.io ───────┐ │
            └─────────────────────────────────────│ FastAPI ─► Postgres   │─┘
                                                  │           (pgvector)  │
                                                  └───────────────────────┘
```

---

## 1. Prerequisites (one-time)

You need **three free accounts** and **two CLI tools**.

### 1.1 Accounts
1. **GitHub** — the repo must be pushed to GitHub (it already is: `github.com/sks17/PaperPigeon`).
2. **fly.io** — go to <https://fly.io/app/sign-up>, click **Sign up**, verify your email.
   - fly.io requires a **credit card on file** even for the free allowance. Add it at
     <https://fly.io/dashboard> → **Billing** → **Add credit card**.
3. **Vercel** — go to <https://vercel.com/signup>, click **Continue with GitHub**, authorize.

### 1.2 CLI tools
1. **flyctl** (the fly.io CLI):
   - **Windows (PowerShell):** `pwr -Command "iwr https://fly.io/install.ps1 -useb | iex"`
     (or: `powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"`)
   - **macOS / Linux:** `curl -L https://fly.io/install.sh | sh`
   - Close and reopen your terminal, then verify: `fly version`
2. **Node 20+ and pnpm** (only needed if you build the frontend locally; Vercel builds it for you):
   `npm install -g pnpm`

### 1.3 Log in to fly
```bash
fly auth login
```
A browser window opens → click **Continue** / **Authorize** → return to the terminal (it prints
`successfully logged in`).

---

## 2. Deploy the backend API to fly.io

> Run every command **from the repository root** (the folder containing `fly.toml` and `Dockerfile`).

### 2.1 Create the fly app
```bash
fly apps create paper-pigeon-api
```
- If the name is taken, pick another (e.g. `paper-pigeon-api-yourname`) **and** update the `app =`
  line at the top of `fly.toml` to match.

### 2.2 Provision a Postgres database **with pgvector**

The app needs Postgres **with the `vector` extension** (pgvector). Pick **one** option:

#### Option A — Neon (recommended: pgvector is built in, generous free tier)
1. Go to <https://neon.tech> → **Sign up** (Continue with GitHub).
2. Click **Create project** → name it `paper-pigeon` → Postgres 16 → region close to you → **Create**.
3. On the project dashboard, click **Connection string** → copy the `postgresql://…` URL
   (the "pooled" or direct connection both work; use the one labeled **Direct** for migrations).
4. Give it to the fly app as a secret (replace the URL):
   ```bash
   fly secrets set DATABASE_URL="postgresql://user:pass@ep-xxx.neon.tech/paper-pigeon?sslmode=require" --app paper-pigeon-api
   ```
   pgvector is preinstalled on Neon, so the `CREATE EXTENSION vector` in the migration just works.

#### Option B — Fly Postgres (keeps everything on fly)
```bash
fly postgres create --name paper-pigeon-db --region sea --vm-size shared-cpu-1x --volume-size 1
```
- Answer the prompts (a small **Development** single-node cluster is fine).
- **Save the credentials it prints once** (you can't see the password again).
- Attach it (this sets `DATABASE_URL` on the app automatically):
  ```bash
  fly postgres attach paper-pigeon-db --app paper-pigeon-api
  ```
- Fly's `postgres-flex` image bundles pgvector, so `CREATE EXTENSION vector` works. If a deploy later
  errors with `extension "vector" is not available`, switch to Option A (Neon) instead.

### 2.3 (Optional) Secrets for running data jobs from this app
The read API needs **only** `DATABASE_URL`. You only need these if you'll run repopulation/describe
jobs (Section 5) inside the fly machine:
```bash
fly secrets set --app paper-pigeon-api \
  OPENALEX_API_KEY=your_openalex_key \
  OPENROUTER_API_KEY=your_openrouter_key \
  PAPERPIGEON_BUDGET_PRO_DAILY_USD=10
```
> **Never** put these in `fly.toml`, `.env`, or git. `fly secrets` stores them encrypted.

### 2.4 Deploy
```bash
fly deploy --app paper-pigeon-api
```
What happens (watch the output):
1. Builds the `Dockerfile` (installs the engine deps).
2. Runs the **release command** `python scripts/prod_migrate_seed.py` — applies the migrations and
   seeds the legacy graph (323 nodes). You'll see `seeded legacy graph: …`.
3. Starts the machine and waits for the `/health` check to pass.

When it finishes it prints your URL, e.g. `https://paper-pigeon-api.fly.dev`.

### 2.5 Verify the backend
```bash
curl https://paper-pigeon-api.fly.dev/health
# {"status":"ok"}

curl https://paper-pigeon-api.fly.dev/api/graph/data | head -c 200
# {"nodes":[...],"links":[...]}   (the full graph)
```
Or open `https://paper-pigeon-api.fly.dev/health` in a browser. **Copy your fly URL — you need it in
Section 3.**

> Scale-to-zero is on (`fly.toml` → `auto_stop_machines`), so the first request after idle takes a
> few seconds to wake. That's expected and free.

---

## 3. Point the frontend at the backend (Vercel)

The repo already deploys to Vercel from GitHub. You just need to route the new endpoints to fly.
**Recommended:** edit `vercel.json` so the frontend keeps calling relative URLs.

### 3.1 Edit `vercel.json`
Open `vercel.json` and add three rewrites **above** the existing catch-all `/api/(.*)` rule, replacing
the host with **your** fly URL from Step 2.5:

```json
{
  "rewrites": [
    { "source": "/api/graph/data",       "destination": "https://paper-pigeon-api.fly.dev/api/graph/data" },
    { "source": "/api/node/description",  "destination": "https://paper-pigeon-api.fly.dev/api/node/description" },
    { "source": "/api/lab",               "destination": "https://paper-pigeon-api.fly.dev/api/lab" },
    { "source": "/api/runs",              "destination": "https://paper-pigeon-api.fly.dev/api/runs" },
    { "source": "/api/(.*)",              "destination": "/api/index.py" },
    { "source": "/(.*)",                  "destination": "/index.html" }
  ],
  "functions": { "api/index.py": { "includeFiles": "backend/**,public/graph_cache.json" } },
  "ignoreCommand": "echo 'No ignore needed'"
}
```
Order matters: the specific `/api/graph/data` etc. must come **before** `/api/(.*)`, and the SPA
catch-all `/(.*)` stays **last**. Commit and push:
```bash
git add vercel.json && git commit -m "Route graph/description/lab/runs to the fly backend" && git push
```

> Alternative (no `vercel.json` edit): set `VITE_API_BASE_URL=https://paper-pigeon-api.fly.dev` in
> Vercel (Step 3.3). That sends graph/description/lab/runs straight to fly; `paper-lab-id` and the
> AWS endpoints stay on Flask automatically. Use this only if you don't want path rewrites.

### 3.2 Connect the repo to Vercel (first time only)
1. Go to <https://vercel.com/new>.
2. Under **Import Git Repository**, find `PaperPigeon` → click **Import**.
   (If you don't see it, click **Adjust GitHub App Permissions** and grant access to the repo.)
3. **Configure Project** screen:
   - **Framework Preset:** Vercel auto-detects **Vite**. Leave it.
   - **Build Command:** `pnpm build` (auto-filled).
   - **Output Directory:** `dist` (auto-filled).
   - **Install Command:** `pnpm install` (auto-filled).
4. (If your app uses the AWS features) expand **Environment Variables** and add the backend ones the
   Flask function needs — click **Add** for each: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   `AWS_REGION`, `S3_BUCKET_NAME`, `BEDROCK_KNOWLEDGE_BASE_ID`, `BEDROCK_KNOWLEDGE_BASE_ID_2`
   (values from your AWS setup; see `VERCEL_ENV_VARIABLES.md`). Set **Environment** to **Production**.
5. Click **Deploy**. Wait for the build → you get a `https://paper-pigeon-xxxx.vercel.app` URL.

> Already connected? Then pushing `vercel.json` in Step 3.1 auto-triggers a new deploy — no clicks
> needed. Watch it at <https://vercel.com/dashboard> → your project → **Deployments**.

### 3.3 (Only if using the env-var alternative) add VITE_API_BASE_URL
1. Vercel dashboard → your project → **Settings** → **Environment Variables**.
2. **Key:** `VITE_API_BASE_URL`  **Value:** `https://paper-pigeon-api.fly.dev`  **Environment:** Production.
3. Click **Save**, then **Deployments** → **⋯** on the latest → **Redeploy**.

---

## 4. Verify the whole thing end-to-end

1. Open your Vercel URL (e.g. `https://paper-pigeon-xxxx.vercel.app`).
2. The graph renders (323 nodes) — now served by fly via the rewrite.
3. Open the browser **DevTools → Network**, confirm `/api/graph/data` returns 200.
4. (After you've run a describe job — Section 5) the **run-snapshot picker** appears top-left; switch
   to a run and open a researcher → you see the **"Grounded · cites N sources"** panel.
5. RAG chat / recommendations / PDF links still work (still served by Flask on Vercel).

---

## 5. Populate grounded data (so the new features have something to show)

Out of the box the published graph has no AI descriptions (they're additive). To create them you run
the pipeline against the **same `DATABASE_URL`** your fly app uses. Easiest from your laptop:

```bash
# from the repo root, with .venv active and OPENALEX_API_KEY + OPENROUTER_API_KEY in .env
# point the tools at the production DB:
export DATABASE_URL="postgresql://…"          # the same URL you set as the fly secret
# (these scripts currently boot a local pgserver; to target prod, run them on the fly machine:)
fly ssh console --app paper-pigeon-api
#   then inside the machine:
#   python scripts/repopulate.py --institution "University of Washington" --topic "computer vision"
#   python scripts/describe.py  --institution "University of Washington" --topic "computer vision" --limit 50
```
A described **run** then shows up in the UI's run picker (`/api/runs`). To make grounded descriptions
appear on the **default** graph, either publish the run or run `scripts/describe.py --promote`.

> Want to *see the UI feature locally first*, with no API keys? Run
> `python scripts/run_local_stack.py --demo` and open `http://localhost:5173` (with `pnpm dev` in
> another terminal). It seeds a grounded demo run so the picker + provenance panels are populated.

---

## 5b. Enable on-demand discovery (search ANY ecosystem from the app)

The deploy already ships two process groups (`fly.toml [processes]`): the **web** API and an
always-on **worker** that runs discovery jobs. To turn the feature on:

1. **Set the gate key** (any strong random string — this is what users type in the app's Discover box):
   ```bash
   fly secrets set --app paper-pigeon-api DISCOVERY_API_KEY="$(openssl rand -hex 24)"
   # also ensure the worker can call the paid APIs:
   fly secrets set --app paper-pigeon-api OPENALEX_API_KEY=… OPENROUTER_API_KEY=… PAPERPIGEON_BUDGET_PRO_DAILY_USD=10
   ```
2. **Deploy** (if not already): `fly deploy --app paper-pigeon-api`.
3. **Ensure one machine of each process** (the worker must stay up to drain the queue):
   ```bash
   fly scale count web=1 worker=1 --app paper-pigeon-api
   fly status --app paper-pigeon-api          # confirm a 'worker' machine is running
   fly logs --app paper-pigeon-api            # 'discovery worker … started'
   ```
4. **Use it** — in the web app click **Discover** (top-left), enter an institution (+ optional topic),
   paste the `DISCOVERY_API_KEY`, and submit; the new run auto-selects when the worker finishes. Or via curl:
   ```bash
   curl -X POST https://paper-pigeon-api.fly.dev/api/discover \
     -H "X-Discovery-Key: <key>" -H "Content-Type: application/json" \
     -d '{"institution":"Massachusetts Institute of Technology","topic":"robotics","scrape":false}'
   # → {"job_id":…, "run_id":null, "status":"queued", "cached":false}
   curl https://paper-pigeon-api.fly.dev/api/discover/<job_id> -H "X-Discovery-Key: <key>"
   ```

Cost & safety: every discovery spends OpenAlex + OpenRouter credits, bounded by the per-job page caps
(`DISCOVERY_MAX_AUTHOR_PAGES`, etc.) and the atomic daily `PAPERPIGEON_BUDGET_PRO_DAILY_USD` ledger.
**Near-term follow-up:** the single shared key has no per-caller quota — add per-key quotas / rate
limiting before exposing it widely. Treat the key as a shared secret (rotate with `fly secrets set`).

## 6. Updating after the first deploy

- **Backend code change:** `git push`, then `fly deploy --app paper-pigeon-api`.
  (The release command re-applies migrations idempotently and skips re-seeding.)
- **Frontend change:** just `git push` — Vercel auto-builds and deploys.
- **New DB migration:** add `backend/repopulation/migrations/000N_*.sql`; the next `fly deploy`
  applies it via the release command.

---

## 7. Rollback

- **Undo the routing only** (fastest): remove the three fly rewrites from `vercel.json` (or unset
  `VITE_API_BASE_URL`) and push / redeploy. All `/api/*` returns to the Flask backend. The site is
  exactly as it was before this guide.
- **Roll back a bad backend deploy:** `fly releases --app paper-pigeon-api` (list), then
  `fly deploy --image <previous-image-ref> --app paper-pigeon-api`, or `fly apps restart paper-pigeon-api`.
- **Tear it all down:** `fly apps destroy paper-pigeon-api` and (Option B) `fly postgres destroy
  paper-pigeon-db`. Neon: delete the project in its dashboard. Nothing on Vercel is affected.

---

## 8. Troubleshooting

| Symptom | Fix |
|--------|-----|
| `fly deploy` fails at release with `extension "vector" is not available` | Your Postgres lacks pgvector. Use **Neon** (Section 2.2 Option A). |
| `/api/graph/data` 500 / empty | Release command didn't seed. `fly logs --app paper-pigeon-api`; check `DATABASE_URL` is set (`fly secrets list`). Re-run: `fly ssh console -C "python scripts/prod_migrate_seed.py"`. |
| Graph loads but no run picker | Expected until a repopulation run exists (Section 5). The picker self-hides with zero runs. |
| Vercel build fails on `pnpm lint` | The repo's lint is independent of deploy; Vercel runs **build** (`pnpm build`), not lint. If you added a lint step, the gate is `pnpm build`. |
| First request after idle is slow | Scale-to-zero cold start (~2–5s). Expected. To disable: set `min_machines_running = 1` in `fly.toml`. |
| CORS error in browser | The API sets `Access-Control-Allow-Origin: *`; if you proxied via Vercel rewrites you won't hit CORS at all. Prefer the rewrite path (Section 3.1). |

---

## 9. Cost (free-tier friendly)

- **fly.io:** one `shared-cpu-1x` 512 MB machine with scale-to-zero ≈ free/near-free for low traffic.
- **Neon:** free tier covers this dataset comfortably. (Fly Postgres dev cluster: a small monthly cost.)
- **Vercel:** Hobby tier is free for this SPA + serverless function.
- **OpenAlex/OpenRouter:** only charged when you run repopulation/describe jobs; capped by
  `PAPERPIGEON_BUDGET_PRO_DAILY_USD` (Section 2.3).
