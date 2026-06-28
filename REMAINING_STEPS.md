# Paper Pigeon — Deployment status & remaining steps

_Backend is live on fly.io. The frontend (Vercel) is the only thing between you and a fully working
public site._

## ✅ Done for you

- **Backend live** at **https://paper-pigeon-api.fly.dev** — app `paper-pigeon-api` + Postgres
  `paper-pigeon-db`, web (scale-to-zero) + always-on worker, migrations through 0004, graph seeded
  (323/1043). Secrets set: `DATABASE_URL`, `OPENALEX_API_KEY`, `OPENROUTER_API_KEY`, `DISCOVERY_API_KEY`.
- **Verified in production:** auth gate (401 without key), and a real *University of Toronto* discovery
  ran on the deployed worker → run #2 (200 researchers, 80 grounded descriptions).
  - ⚠️ That run #2 was built with the **old** discovery code (sparse co-authorship, no labs). It has
    been **recreated with the new approach** and now ships as a committed example snapshot
    (`backend/repopulation/examples/university_of_toronto.json`), seeded idempotently on every deploy
    by `scripts/prod_migrate_seed.py` → `examples/seed.py`. The shipped example carries a distinct
    `seed.example=true`, so it seeds as its own canonical run and never collides with run #2.
  - **One-time cleanup on the live DB:** drop the stale run #2 with the guarded cleanup script
    (dry-run first; it refuses to touch the published graph and preserves anything another run shares).
    After deploying so the script is on the machine:
    ```bash
    fly ssh console -a paper-pigeon-api -C "python scripts/cleanup_run.py --run-id 2"        # preview
    fly ssh console -a paper-pigeon-api -C "python scripts/cleanup_run.py --run-id 2 --yes"  # delete
    ```
    (`fly ssh` runs it inside the app, where `DATABASE_URL` is already set.)
  - Regenerate the example anytime with: `python scripts/build_example.py --institution "University of Toronto"`.
- **`vercel.json` rewrites fixed** (it was invalid JSON + missing the discovery routes). It now proxies
  `graph/data`, `node/description`, `lab`, `runs`, `discover`, `discover/:id` to fly, leaving the AWS
  endpoints on Flask. Already committed — you just need to deploy it (Step 2).

---

## Step 1 — Rotate the discovery key (2 minutes)

I set a key for testing; replace it with one only you know. In a terminal (you're already `fly`-logged-in):

```bash
fly secrets set DISCOVERY_API_KEY="$(openssl rand -hex 24)" -a paper-pigeon-api
```
- This prints nothing sensitive and restarts the machines (~30s).
- **Copy the value you generate** — you'll paste it into the app's Discover box. (No `openssl`? Use any
  long random string: `fly secrets set DISCOVERY_API_KEY="pp_live_8charsOrMore_random" -a paper-pigeon-api`.)
- Verify: `fly secrets list -a paper-pigeon-api` shows `DISCOVERY_API_KEY` with a fresh digest.

---

## Step 2 — Deploy the frontend to Vercel (the main step, ~10 min)

### 2a. Push the fixed vercel.json
```bash
git pull                      # get the vercel.json + REMAINING_STEPS.md I committed
# (nothing else to do — vercel.json is already correct in the repo)
```

### 2b. Connect the repo to Vercel (first time only)
1. Go to **https://vercel.com/new** (log in with **Continue with GitHub** if prompted).
2. Under **Import Git Repository**, find **`sks17/PaperPigeon`** → click **Import**.
   - Don't see it? Click **Adjust GitHub App Permissions** → grant Vercel access to that repo → come back.
3. On the **Configure Project** screen, leave the auto-detected values:
   - **Framework Preset:** `Vite`
   - **Build Command:** `pnpm build`
   - **Output Directory:** `dist`
   - **Install Command:** `pnpm install`
   - (Skip Environment Variables for now — see 2d.)
4. Click **Deploy**. Wait ~1–2 min for the build → you get a URL like `https://paper-pigeon-xxxx.vercel.app`.

### 2c. Verify the live site
Open your Vercel URL and check:
- [ ] The 3D graph renders (~323 nodes) — proves `/api/graph/data` is proxying to fly.
- [ ] Click **Discover** (top-left), enter `Carnegie Mellon University`, topic `robotics`, paste your
      key from Step 1, **Discover**. It shows "Discovering…/Describing…", then the new run auto-selects.
      (First run for a big school can take a few minutes — that's OpenAlex throttling cloud IPs.)
- [ ] Open one of the new researchers → the **"Grounded · cites N sources"** panel shows.
- [ ] (Quick gut-check from a terminal) `curl https://YOUR-VERCEL-URL/api/runs` returns the run list.

### 2d. (Optional) Turn on the AWS-backed features
The graph + discovery work **without** this. Only do it if you want RAG chat / PDF links / resume
recommendations (still served by the bundled Flask function):
1. Vercel dashboard → your project → **Settings → Environment Variables**.
2. Click **Add** for each (values from your AWS setup; see `VERCEL_ENV_VARIABLES.md`), **Environment =
   Production**: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `S3_BUCKET_NAME`,
   `BEDROCK_KNOWLEDGE_BASE_ID`, `BEDROCK_KNOWLEDGE_BASE_ID_2`.
3. **Deployments** tab → **⋯** on the latest → **Redeploy**.

> After this, every future `git push` to `main` auto-deploys the frontend on Vercel.

---

## Step 3 — Optional tuning (recommended once it works)

Each `fly secrets set` restarts the worker (~30s; a job running at that moment gets requeued).

- [ ] **Cheaper/faster jobs** (default is 80 descriptions/job):
      ```bash
      fly secrets set DISCOVERY_DESCRIBE_LIMIT=25 DISCOVERY_MAX_AUTHOR_PAGES=1 DISCOVERY_MAX_WORK_PAGES=1 -a paper-pigeon-api
      ```
- [ ] **Daily spend cap** (confirm/raise): `fly secrets set PAPERPIGEON_BUDGET_PRO_DAILY_USD=10 -a paper-pigeon-api`
- [ ] **Faster discovery** (optional): the free OpenAlex tier throttles datacenter IPs hard — a paid key
      makes cloud discovery much faster. Update with `fly secrets set OPENALEX_API_KEY=<paid key> -a paper-pigeon-api`.

---

## Step 4 — Hardening before sharing the key widely (later)

- [ ] **Per-key quotas / rate limiting** — today one shared key; spend is bounded only by per-job caps +
      the daily budget. Add per-caller quotas before handing the key out broadly.
- [ ] **Worker egress filtering** — network-level egress rules on the worker machine to fully close the
      DNS-rebind SSRF window (the app already blocks private/metadata IPs; this is defense-in-depth).
- [ ] **Worker log visibility (minor)** — the worker runs fine but its stdout isn't showing in
      `fly logs`; switch its prints to `logging`→stderr so you can watch job progress.

---

## Cheat sheet

```bash
fly status  -a paper-pigeon-api                    # web + worker machines
fly logs    -a paper-pigeon-api                    # tail logs
fly secrets list -a paper-pigeon-api               # what's configured (digests only)
fly deploy  -a paper-pigeon-api                    # redeploy backend after a code change
# trigger discovery from the CLI (sanity check the backend without the UI):
curl -X POST https://paper-pigeon-api.fly.dev/api/discover \
  -H "X-Discovery-Key: <your key>" -H "Content-Type: application/json" \
  -d '{"institution":"Carnegie Mellon University","topic":"robotics"}'
curl https://paper-pigeon-api.fly.dev/api/discover/<job_id> -H "X-Discovery-Key: <your key>"
```
