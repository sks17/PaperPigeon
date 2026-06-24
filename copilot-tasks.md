# Copilot — sequential. Work top to bottom, one at a time. Check off when done.
# Lowest-level only: glue, boilerplate, mechanical refactors, docstrings, formatting,
# and actioning OpenClaw cleanup-report items. NO parallelism, NO structural changes,
# NO API integration, NO running commands. Each item must be obviously correct on read.
# Read AGENTS.md first. Anything non-trivial belongs to Cursor (scoped) or the main thread.

- [x] 1. Apply these factual corrections to `02-api-information.md` (validated against current
        official docs, June 2026 — exact edits, no new analysis):
        a. OpenAlex row — replace "keyed locally via `OPENALEX_API_KEY`" nuance with: an API key
           is **mandatory since 2026-02-13**; the `?mailto=` polite pool no longer applies to
           OpenAlex; keyless requests 409 after ~100 credits.
        b. OpenAlex row — keep the "~$1/day" figure but state pricing is **usage-based credits**:
           $0 single-entity lookups (unlimited) · $0.10/1k list+filter (10k/day) · $1/1k search
           (1k/day) · $10/1k content downloads (100/day); free key = $1 credit/day.
        c. OpenAlex row — CORRECT the abstract note: data is **CC0 and redistributable**; the
           inverted index is a delivery format, not a license restriction. Remove "don't
           redistribute casually."
        d. OpenAlex docs link — note `docs.openalex.org` now redirects to `developers.openalex.org`.
        e. ROR row — add: register a free **client ID before Q3 2026** to keep 2,000 req/5min
           (else it drops to 50/5min).
        f. Crossref row — add concrete post-2025-12-01 limits: polite pool 10 req/s single-DOI,
           3 req/s lists (concurrency 3); public pool 5/1 req/s.
        (Leave arXiv, PubMed, Semantic Scholar, ORCID text as-is — confirmed still accurate.)

- [x] 2. Create `backend/repopulation/README.md`: a short package-layout overview copied from the
        module map in `backend/repopulation/__init__.py`, with a one-line pointer to `SCHEMA.md`
        (the data contract) and `migrations/0001_initial.sql` (the schema). No new content/claims.

- [x] 3. Read-only repo map pass: inventory modules, entrypoints, and ownership boundaries across
   `src/`, `backend/`, `api/`, and top-level docs. Produce a concise map in
   `docs/repo-readability-audit.md` with file paths and one-line responsibilities.

- [x] 4. Read-only Python structure pass: inspect classes/functions in `backend/` and flag where
   declaration order harms readability (helpers mixed with public APIs, lifecycle flow split,
   unclear grouping). Record candidate reorder plans only; no edits in this task.

- [x] 5. Read-only TypeScript structure pass: inspect components/services in `src/` and flag where
   method/comment placement makes intent hard to follow. Record candidate reorder/comment
   cleanup plans only; no edits in this task.

- [ ] 6. Legibility edit pass (Python): reorder methods and nearby comments in the highest-value
   Python files to follow a consistent reading flow (public API first, helpers next, internals
   last). Do not change behavior, signatures, or logic.

- [ ] 7. Legibility edit pass (TypeScript): move comments and reorder functions/method blocks in
   selected frontend files for clarity (state/setup, handlers, render/helpers). Do not change
   behavior, props/contracts, or data flow.

- [ ] 8. Comment quality pass: rewrite or relocate misleading/stale comments and add short,
   high-signal comments where preconditions/postconditions are implicit. Keep comments factual,
   minimal, and adjacent to the relevant code.

- [ ] 9. Read-only anti-pattern scan: review the repo and flag potential poor practices with evidence,
   including God Classes, unclear preconditions/postconditions, weak module boundaries, mixed
   responsibilities, and hidden side effects.

- [ ] 10. Read-only findings report: add a section to `docs/repo-readability-audit.md` with each
   flagged issue, severity (low/med/high), exact file references, why it matters, and a small
   remediation suggestion.

- [ ] 11. Traceability pass: for every file changed in tasks 6-8, document before/after rationale in
   `docs/repo-readability-audit.md`, confirming edits were organizational/comment-only and not
   behavioral.

- [ ] 12. Final review checklist: verify task-order completion, ensure no structural/API changes were
   introduced, and summarize remaining high-risk design/code-quality concerns that should be
   escalated to Cursor or the main thread.
