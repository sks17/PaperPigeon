/**
 * Docs content — release notes & design changes, rendered github.io-style by src/pages/Docs.tsx.
 *
 * EASY TO UPDATE: add a new object to the top of `docEntries`. Within `blocks`, use:
 *   { kind: 'h',    text }            → a subheading
 *   { kind: 'p',    text }            → a paragraph
 *   { kind: 'list', items: [...] }    → a bulleted list
 * Inline, **double asterisks** render bold and `backticks` render code. That's the whole format.
 */

export type Block =
  | { kind: 'h'; text: string }
  | { kind: 'p'; text: string }
  | { kind: 'list'; items: string[] };

export interface DocEntry {
  id: string;
  version: string;
  title: string;
  date: string;
  /** Optional badge, e.g. "Current version". */
  status?: string;
  /** One-line summary shown under the title. */
  lede: string;
  blocks: Block[];
}

export const docEntries: DocEntry[] = [
  {
    id: 'beta-0-5',
    version: 'Beta 0.5',
    title: 'The current version',
    date: 'June 2026',
    status: 'Current version',
    lede: "What changed between the README's architecture and the system running today.",
    blocks: [
      { kind: 'h', text: 'The short version' },
      {
        kind: 'p',
        text:
          "The README still describes the **original** Paper Pigeon: one fixed snapshot of the UW " +
          'Allen School, pre-computed into a static cache and rebuilt on a schedule. Beta 0.5 keeps ' +
          'that graph as the home view, but adds a **Repopulation Engine** — an on-demand pipeline ' +
          'that can ingest *any* research ecosystem and build a fresh, grounded graph for it on the spot.',
      },

      { kind: 'h', text: 'What the README describes (the original design)' },
      {
        kind: 'list',
        items: [
          '**Scope** — the UW Allen School only; a single fixed corpus.',
          '**Data** — researchers, papers, and edges in **DynamoDB**, with the whole graph pre-computed into a static `graph_cache.json`.',
          '**Freshness** — a **Cloudflare** cron rebuilds that cache on a schedule.',
          '**AI** — paper chat and resume matching via **AWS Bedrock**; PDFs served from **S3**.',
          '**Backend** — a **Flask** API running as **Vercel** serverless functions.',
        ],
      },

      { kind: 'h', text: "What's different in beta 0.5 (today)" },
      {
        kind: 'list',
        items: [
          '**On-demand discovery of any ecosystem.** Name an institution and the engine resolves it (**ROR**), pulls its researchers and works (**OpenAlex**), and assembles a scoped graph — query-conditioned and user-triggered, not a nightly job over a fixed corpus.',
          '**Estimated labs and real connections.** Lab affiliations are inferred from co-authorship communities (anchored on a likely PI) when no lab page exists, and co-authorship is fetched aligned to the discovered cohort — so a brand-new institution comes back as a connected network instead of scattered dots.',
          '**Grounded AI descriptions.** Researcher and lab write-ups are generated from cited evidence using **OpenRouter** models and embeddings, replacing the Bedrock/DynamoDB dependency for the graph itself.',
          '**A real data store.** The graph and discovery API now run on **FastAPI + Postgres** (pgvector) on **fly.io**, with an always-on worker draining a discovery queue. The graph is served from Postgres **run snapshots** — each node and edge typed, weighted, and provenance-bearing — not only a static file.',
          '**Run snapshots and a picker.** The published UW graph stays the default; discovered runs (**University of Toronto** and **MIT** ship as built-in examples) are selectable from the run picker inside the search bar.',
          '**Routing.** Vercel proxies the graph and discovery routes to fly; the AWS-backed extras (Bedrock chat, S3 PDFs, resume matching) still live on the bundled Flask function — optional and credential-gated.',
        ],
      },

      { kind: 'h', text: 'Still true from the README' },
      {
        kind: 'list',
        items: [
          'The UW Allen School graph is the default home view.',
          'A 3-D force-directed visualization plus an immersive VR mode — now at `/app` and `/vr`.',
          'React 19 + Vite + Tailwind on the front end; the AWS features remain available when credentials are provided.',
        ],
      },

      { kind: 'h', text: 'Caveats' },
      {
        kind: 'p',
        text:
          'This is a **beta**, mid-migration. Discovery is key-gated, the AWS extras need ' +
          'credentials, and the README documents the pre-rework system — treat it as history until ' +
          'it catches up to the design above.',
      },
    ],
  },
];
