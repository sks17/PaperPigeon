# Paper Pigeon — Deployment status & remaining steps

_Last updated after deploying the discovery service to fly.io._

## ✅ Done for you (backend on fly.io)

The **backend is fully live and verified** at **https://paper-pigeon-api.fly.dev**:

- App `paper-pigeon-api` + managed Postgres `paper-pigeon-db` (attached → `DATABASE_URL` set).
- Latest code deployed (the on-demand discovery service). Process model is now **`web`** (the API,
  scale-to-zero) + **`worker`** (always-on, drains the discovery queue) — `fly status` shows web +
  worker machines `started`.
- Migrations applied through **0004** via the release command; the legacy graph is seeded
  (`/api/graph/data` → 323 nodes / 1043 links).
- Secrets set: `DATABASE_URL`, `OPENALEX_API_KEY`, `OPENROUTER_API_KEY`, and a new
  **`DISCOVERY_API_KEY`** (the gate for the Discover box).
- **Verified end-to-end in production:** auth gate returns 401 without the key; a real
  *University of Toronto · machine learning* discovery ran on the deployed worker and produced run #2
  (200 researchers, 80 grounded descriptions). Cache-hit + the failure path are covered by tests.

> Operational note: discovery from a cloud IP is **slower than local** — OpenAlex throttles datacenter
> IPs, so the discover phase took ~7 min for a large institution (vs ~15s locally). It still completes;
> the per-job caps + daily budget make it safe. See "Optional tuning" below to speed it up / cut cost.

---

## 🔑 First: secure your discovery key

I generated and set a `DISCOVERY_API_KEY` (its value is in our chat). **Rotate it to a value you
control** so only you have it:

```bash
fly secrets set DISCOVERY_API_KEY="$(openssl rand -hex 24)" -a paper-pigeon-api
fly secrets list -a paper-pigeon-api          # confirm it's set (digest only)
```
Keep this key private — it's what lets someone spend your OpenAlex/OpenRouter credits via Discover.

---

## ⏳ Remaining step 1 — Deploy the frontend to Vercel (the main one)

Nothing is on Vercel yet, so there's no website. The full click-by-click is in **`DEPLOY.md` §3**;
the checklist:

- [ ] **Route the new endpoints to fly.** Edit `vercel.json` to add these rewrites **above** the
      existing `/api/(.*)` rule (so graph/description/lab/runs/discover hit fly, AWS endpoints stay on
      Flask). Commit + push:
      ```json
      { "source": "/api/graph/data",      "destination": "https://paper-pigeon-api.fly.dev/api/graph/data" },
      { "source": "/api/node/description", "destination": "https://paper-pigeon-api.fly.dev/api/node/description" },
      { "source": "/api/lab",             "destination": "https://paper-pigeon-api.fly.dev/api/lab" },
      { "source": "/api/runs",            "destination": "https://paper-pigeon-api.fly.dev/api/runs" },
      { "source": "/api/discover",        "destination": "https://paper-pigeon-api.fly.dev/api/discover" },
      { "source": "/api/discover/(.*)",   "destination": "https://paper-pigeon-api.fly.dev/api/discover/$1" }
      ```
      (Keep `/api/(.*) → /api/index.py` and `/(.*) → /index.html` last.)
- [ ] **Import the repo:** https://vercel.com/new → **Continue with GitHub** → find **PaperPigeon** →
      **Import**. Framework auto-detects **Vite** (build `pnpm build`, output `dist`). Click **Deploy**.
- [ ] **(Only if you want RAG chat / PDF / recommendations)** add the AWS env vars in Vercel
      → Settings → Environment Variables (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`,
      `S3_BUCKET_NAME`, `BEDROCK_KNOWLEDGE_BASE_ID`, `BEDROCK_KNOWLEDGE_BASE_ID_2` — see
      `VERCEL_ENV_VARIABLES.md`), then redeploy. The graph + discovery features work without these.
- [ ] **Verify:** open the Vercel URL → graph renders (323 nodes) → click **Discover**, enter an
      institution + your key → the new run appears in the picker → open a researcher → grounded
      "cites N sources" panel.

> Alternative to the rewrites: set `VITE_API_BASE_URL=https://paper-pigeon-api.fly.dev` in the Vercel
> project env and redeploy (DEPLOY.md §3.3). The graph/discovery calls go straight to fly;
> `paper-lab-id` stays on Flask.

---

## ⏳ Remaining step 2 — Optional tuning (recommended for cost/speed)

Discovery is bounded but defaults to 80 descriptions/job and can be slow from the cloud. To make jobs
faster + cheaper, set worker env (a `fly secrets set` triggers a worker restart):

- [ ] `fly secrets set DISCOVERY_DESCRIBE_LIMIT=25 DISCOVERY_MAX_AUTHOR_PAGES=1 DISCOVERY_MAX_WORK_PAGES=1 -a paper-pigeon-api`
- [ ] Confirm/raise the daily cap: `fly secrets set PAPERPIGEON_BUDGET_PRO_DAILY_USD=10 -a paper-pigeon-api`
- [ ] (Optional) If discovery is too slow, get a **paid-tier OpenAlex key** (datacenter IPs are
      throttled on the free tier) and update `OPENALEX_API_KEY`.

---

## ⏳ Remaining step 3 — Hardening follow-ups (before opening it up widely)

- [ ] **Per-key quotas / rate limiting** — the single shared key has no per-caller cap; today only the
      per-job caps + daily budget bound spend. Add per-key quotas before sharing the key broadly.
- [ ] **Worker egress filtering** — add network egress rules on the worker to fully close the
      sub-millisecond DNS-rebind SSRF window (the app already blocks private/metadata IPs; this is
      defense-in-depth).
- [ ] **Worker log visibility (minor):** the worker functions correctly but its stdout isn't surfacing
      in `fly logs` yet — useful to fix for observability (e.g. log via `logging` to stderr).

---

## Handy commands

```bash
fly status   -a paper-pigeon-api          # machines (web + worker)
fly logs     -a paper-pigeon-api          # tail logs
fly secrets  list -a paper-pigeon-api     # what's configured
fly scale    count web=1 worker=1 -a paper-pigeon-api   # ensure one of each
fly deploy   -a paper-pigeon-api          # redeploy after a code change
# trigger discovery from the CLI:
curl -X POST https://paper-pigeon-api.fly.dev/api/discover \
  -H "X-Discovery-Key: <your key>" -H "Content-Type: application/json" \
  -d '{"institution":"Carnegie Mellon University","topic":"robotics"}'
```

When the frontend is on Vercel, the whole loop is live: **type any university → watch it get
discovered → explore its grounded research network.**
