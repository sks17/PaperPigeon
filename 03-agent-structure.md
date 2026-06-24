# Agent Structure

*Part 3 of 4 — see also: Product Overview, API Information, Infrastructure/Security/Roadmap.*

The build-time coding-agent org. This is the swarm that **builds** the product; it's separate from the product's runtime AI (which lives in Product Overview → AI features). Governing principle from the subagent material: few, sharp agents; most read-only; authority concentrated at the top; capability matched to scope.

---

## The capability ladder

Top = most capable and most authority. Crucially, **only the top tier does "meta-level" work** — planning, API integration, and running commands.

| Tier | Who | Can do hard work? | Writes code? | Meta-level (planning / API integration / running commands)? |
|---|---|---|---|---|
| Orchestrator | **Claude Code (main thread)** | Yes | Yes | **Yes — exclusively** |
| Scouts / reviewers | **Claude subagents** (≤5–6) | Yes, scoped | Read-mostly | No |
| Implementers | **Cursor agents** (≤10, looping) | **Yes — frontier models** | Yes, within a scoped task | **No** |
| Cleanup | **OpenClaw** | — | **No — read-only** | No |
| Low-level | **Copilot** | No | Yes, small/sequential | No |

---

## Claude Code — main thread (orchestrator)

Architect, integrator, and merge authority. The **only** agent that does meta-level work:

- **Planning & architecture** — the system design, the branch plan, what every lower tier is told to do.
- **API integration** — wiring OpenAlex / ROR / arXiv / Crossref / PubMed and the OpenRouter / Gemini clients (see API Information). Lower tiers consume the internal clients Claude builds; they never wire external APIs themselves.
- **Running commands** — migrations, installs, deploys, and **all test runs**.

It reads the command-center file first and owns merges. Anything requiring judgment about the system as a whole stops here.

---

## Claude subagents (≤5–6, mostly read-only)

Specialists that return structured memos. Each has a defined output format, an "Obstacles Encountered" section, and only the tools it needs:

- `repo-cartographer` (Read/Grep/Glob) — maps the existing service; says where changes belong and what must not be touched.
- `data-source-researcher` (web/read) — returns a source matrix: coverage, rate limits, license, affiliation quality, recommended use.
- `schema-reviewer` (read + bash for static checks) — audits node/edge model, dedupe, provenance.
- `security-reviewer` (read + bash) — secrets, SSRF, scraping injection, webhook/auth, MCP scope; returns critical / high / concrete-fixes / approval.
- `code-reviewer` (Bash/Read/Grep/Glob) — reviews diffs as if written by someone else.
- `test-strategist` (read-only) — produces the per-layer test plan and the cases that matter (see Testing).
- *(optional)* `graph-ui-reviewer` — guards the existing frontend's behavior as the backend swaps under it.

---

## Cursor agents — the parallel implementation pool

Up to **ten Cursor agents loop continuously**, each pulling the next available task from a single task-pool markdown. They run **frontier models**, so they can implement genuinely hard things — complex components, non-trivial algorithms, real module logic, and tests. But they are **walled off from meta-level work**:

- ❌ **No API integration.** Claude wires external APIs; Cursor consumes the already-built internal client.
- ❌ **No planning or architecture decisions.**
- ❌ **No running commands** — no migrations, installs, deploys, or test runs. Claude runs everything.
- ✅ **Pure, scoped implementation** inside a module Claude has already scaffolded, against a precise spec with checkable acceptance criteria.

Because ten run at once, **tasks must be parallel-safe**: each owns a disjoint set of files (no two open tasks touch the same file), declares what's forbidden, and has acceptance criteria a reviewer can verify *without running it*. Finish a task → grab the next unclaimed one.

### `cursor-tasks.md` — the task pool (≤10 parallel, scoped)
```
### TASK-ID: short-name        [status: open | claimed | done]
Layer:            frontend | api | engine | graph | rag | infra
Goal:             one sentence.
Files allowed:    explicit list — MUST be disjoint from every other open task
Files forbidden:  everything else, especially shared config / API clients / migrations
Depends on:       TASK-IDs that must be `done` first (keep empty where possible)
Acceptance:       criteria checkable WITHOUT running commands
Do NOT:           integrate external APIs · run commands · change schema/migrations ·
                  edit another task's files · make architecture decisions
```
Disjoint `Files allowed` across open tasks is what makes ten looping agents safe. Anything with a real dependency chain either gets a `Depends on` gate or stays in the main thread. Map each task's files to one branch (below) to keep merges clean.

---

## OpenClaw — read-only code cleaning

OpenClaw has **read-only access and is used only for code cleaning.** It scans for dead code, inconsistent style, duplicated logic, unused imports/dependencies, and naming drift, and emits a **cleanup report** — it cannot edit anything. Its output is *input*: each cleanup item drops into Copilot's sequential list (or, if non-trivial, becomes a Cursor task). Treat it as a linter with judgment, not an actor.

---

## Copilot — sequential low-level worker

Copilot does the **lowest-level work**: boilerplate, glue, mechanical refactors, docstrings, formatting, small fixes — including actioning OpenClaw's cleanup report. Its tasks live in **a single markdown that defines them sequentially**: an ordered checklist worked top to bottom, one at a time, **no parallelism**. Each item is small enough to be obviously correct on read.

### `copilot-tasks.md` — sequential checklist
```
# Copilot — sequential. Work top to bottom, one at a time. Check off when done.
# Lower-level only: glue, boilerplate, refactors, docstrings, formatting, cleanup-report items.
- [ ] 1. ...
- [ ] 2. ...
- [ ] 3. ...
```

---

## Testing — an agent or two per layer

Every layer gets dedicated test authorship, not best-effort coverage. The work is split to respect the ladder: **strategy** is a Claude subagent, **authoring** is Cursor, **running** is the Claude main thread.

| Layer | Dedicated test tasks (1–2 each, Cursor) |
|---|---|
| Frontend / graph UI | component render + interaction tests |
| API | endpoint contract + auth tests |
| Repopulation engine | idempotency, dedup, quarantine, resume-from-cache |
| Scraping + extraction | fixture-HTML → schema extraction; prompt-injection-safety cases |
| Graph / data | edge integrity, provenance presence on every node/edge |
| RAG / AI descriptions | grounding/citation presence; "no evidence → no claim" |
| Infra / CI | test *plan* by `test-strategist`; runs wired into CI by Claude |

Division of labor:
- **Test design / strategy** → `test-strategist` Claude subagent (read-only) writes the per-layer plan and the cases that matter.
- **Test authoring** → Cursor tasks (frontier models write the actual tests, scoped per layer, file-disjoint, looped from the pool).
- **Test running** → **Claude main thread only.** Running commands is meta-level, and per the subagent material you never hide test output behind a runner subagent — the full failure output must reach the thread that fixes it.

---

## Command center + branches

- **Command-center file** (`AGENTS.md` / `agent-command-center.md`) is the single source of truth: mission, current architecture, non-negotiable constraints, branch plan, a pointer to `cursor-tasks.md`, a pointer to `copilot-tasks.md`, security rules, and the canonical test commands. Claude reads it first; Cursor tasks are excerpts of it; Copilot follows its list; OpenClaw reports against its constraints.
- **Branch plan** — map each Cursor task's `Files allowed` to one branch so parallel work stays merge-clean:
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

---

## Anti-patterns to refuse

- **Agent pipelines** (A→B→C): lossy handoffs; keep dependent debugging in the main thread.
- **Test-runner subagents:** they swallow the output you need — run tests in the main thread.
- **Expert-persona subagents** ("you are a Kubernetes expert"): add nothing Claude doesn't already have.
- **Cursor doing meta work:** if a task needs API wiring, a command, or an architecture call, it's Claude's, not Cursor's — even though Cursor *could* technically attempt it.
- **Overlapping Cursor file scopes:** two looping agents editing one file is merge chaos. Disjoint `Files allowed`, or gate with `Depends on`.
- **Letting OpenClaw or Copilot make structural changes:** OpenClaw is read-only; Copilot is sequential and small. Structural work is Cursor (scoped) or Claude (meta).
