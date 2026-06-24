# Repo Readability Audit

## Task 3: Read-Only Repo Map

Scope covered: `src/`, `backend/`, `api/`, and top-level planning/architecture docs.

### Entrypoints

| Path | Responsibility |
|---|---|
| `src/main.tsx` | Frontend bootstrap that mounts React and loads global styles. |
| `src/App.tsx` | Root UI composition and route-level wiring for graph and VR views. |
| `api/index.py` | Vercel serverless entrypoint that exports Flask `app` for `/api/*`. |
| `api/app.py` | Alternate thin entrypoint that re-exports the same Flask `app`. |
| `backend/app.py` | Main Flask application with graph endpoints, health route, and blueprint registration. |
| `backend/repopulation/api.py` | FastAPI entrypoint for the Postgres-backed repopulation graph API path. |
| `backend_wrapper.py` | Compatibility wrapper entrypoint for environments that import app from project root. |

### Frontend Module Map (`src/`)

| Path | Responsibility |
|---|---|
| `src/components/ResearchNetworkGraph.tsx` | Primary interactive 3D graph screen and user interaction surface. |
| `src/components/VRGraph.tsx` | VR-specific graph rendering route. |
| `src/components/ResearcherModal.tsx` | Researcher detail dialog rendering and actions. |
| `src/components/LabModal.tsx` | Lab detail dialog rendering and actions. |
| `src/components/PaperChatModal.tsx` | UI for paper-grounded chat interactions. |
| `src/components/RecommendationsModal.tsx` | UI for resume-based recommendation flow. |
| `src/components/ResearcherProfilePanel.tsx` | Sidebar/panel view for selected researcher details. |
| `src/components/SearchBar.tsx` | Search and filtering input for graph navigation. |
| `src/components/AccessibilityPanel.tsx` | User controls for accessibility settings in the graph UI. |
| `src/components/ui/` | Shared presentational primitives (button/card/input/etc.). |
| `src/contexts/AccessibilityContext.tsx` | React context/provider for accessibility state and actions. |
| `src/services/dynamodb.ts` | Frontend API client and TypeScript graph contract types. |
| `src/services/pdf.ts` | Frontend helper for PDF URL retrieval and access flow. |
| `src/lib/utils.ts` | Shared frontend utility helpers. |
| `src/types/vr.d.ts` | VR/A-Frame related type declarations for TypeScript compatibility. |

### Backend Module Map (`backend/`)

| Path | Responsibility |
|---|---|
| `backend/graph_core.py` | Pure graph construction from DynamoDB-backed source reads to frontend JSON shape. |
| `backend/controllers/rag_controller.py` | Flask routes for paper Q and A backed by Bedrock RAG service calls. |
| `backend/controllers/recommendations_controller.py` | Flask route for resume-text recommendations. |
| `backend/controllers/pdf_controller.py` | Flask route for generating PDF access URLs. |
| `backend/services/dynamodb_service.py` | DynamoDB access layer for researchers, papers, edges, and supporting records. |
| `backend/services/bedrock_service.py` | Bedrock integration layer for chat and recommendation generation. |
| `backend/services/s3_service.py` | S3 integration layer for presigned document URLs and object access. |
| `backend/build_graph_cache.py` | Utility script to build local graph cache artifacts from backend data. |
| `backend/precompute_graph.py` | Utility script to precompute graph output for deployment-oriented use. |
| `backend/tools/rebuild_graph_cache.py` | Scripted cache rebuild helper under backend tools namespace. |
| `backend/utils/auth.py` | Shared backend auth-related helpers/utilities. |
| `backend/utils/cors.py` | Shared backend CORS utility behavior. |

### Repopulation Package Map (`backend/repopulation/`)

| Path | Responsibility |
|---|---|
| `backend/repopulation/__init__.py` | Package-level summary of Phase 1 scope and module contract pointers. |
| `backend/repopulation/README.md` | Human-readable package layout and pointers to schema contract files. |
| `backend/repopulation/SCHEMA.md` | Data contract for row dictionaries and graph output shape. |
| `backend/repopulation/migrations/` | SQL schema migration assets for repopulation persistence. |
| `backend/repopulation/models/` | SQLAlchemy models aligned to migration-defined schema. |
| `backend/repopulation/serializers/` | Conversion logic from DB rows to frontend graph payload shape. |
| `backend/repopulation/importer/` | Static graph cache to row-dictionary import utilities. |
| `backend/repopulation/sources/` | Pure parser functions for external-source fixture data. |
| `backend/repopulation/discovery/` | Discovery-oriented processing modules for repopulation workflows. |
| `backend/repopulation/relevance/` | Relevance scoring and related transformation utilities. |
| `backend/repopulation/db.py` | Engine/session factory helpers for Postgres access. |
| `backend/repopulation/loader.py` | Graph payload assembly from DB state with compatibility shaping. |
| `backend/repopulation/tests/` | Test suites validating contract, migration, and idempotency behavior. |

### API Surface (`api/`)

| Path | Responsibility |
|---|---|
| `api/index.py` | Vercel runtime hook that exposes the Flask app object. |
| `api/app.py` | Additional app export module for alternative import targets. |

### Top-Level Docs and Planning Assets

| Path | Responsibility |
|---|---|
| `README.md` | Primary project overview, local setup, and usage guidance. |
| `SETUP.md` | Environment/bootstrap specifics for local development. |
| `ARCHITECTURE_ANALYSIS.md` | Current-state architecture notes and system analysis. |
| `FRONTEND_GRAPH_SCHEMA.md` | Cross-language graph payload contract details consumed by frontend. |
| `01-product-overview.md` | Product goals, data model direction, and repopulation framing. |
| `02-api-information.md` | External API landscape, limits, and sourcing guidance. |
| `03-agent-structure.md` | Build-time agent hierarchy, responsibilities, and constraints. |
| `04-infrastructure-security-and-roadmap.md` | Infra topology, security posture, and milestone sequence. |
| `AGENTS.md` | Operational command center and workflow constraints for agent execution. |
| `CLAUDE.md` | Claude-specific repository operating guidance and architecture notes. |
| `cursor-tasks.md` | Parallel implementation task pool intended for Cursor workers. |
| `copilot-tasks.md` | Sequential low-level implementation checklist intended for Copilot. |

### Ownership Boundaries (Current)

| Boundary | Owned by | Notes |
|---|---|---|
| UI composition and interaction | `src/components/`, `src/contexts/` | React components and context own presentation/state behavior. |
| Frontend data-access contract | `src/services/` | Frontend calls backend APIs only through service modules. |
| HTTP route orchestration | `backend/app.py`, `backend/controllers/` | Flask routes mediate request validation and response shape. |
| External service integration | `backend/services/` | AWS integrations are centralized behind service modules. |
| Graph shape construction | `backend/graph_core.py`, `backend/repopulation/loader.py` | Backend graph assembly owns compatibility with frontend schema. |
| Deployment/runtime entry bindings | `api/`, `backend_wrapper.py` | Thin exports provide runtime-specific import paths. |
| Long-horizon architecture intent | top-level `*.md` docs | Planning docs define expected direction and constraints. |

## Task 4: Read-Only Python Structure Pass

Goal: identify declaration-order patterns that reduce readability and propose non-behavioral reordering plans only.

### Candidate Reorder Plans (No Code Changes Applied)

| File | Current readability concern | Candidate declaration order |
|---|---|---|
| `backend/services/dynamodb_service.py` | Public fetch functions are split by an internal helper (`_batch_get`), so readers hit low-level batching logic midway through the API surface. | 1) module constants/cache dicts, 2) private helpers (`_env`, `get_dynamodb`, `_batch_get`), 3) public fetch API grouped by entity (`fetch_researchers`, edges, library, papers, lab/description/metrics). |
| `backend/services/bedrock_service.py` | `rag_chat` is very long and interleaves stage comments with heavy diagnostics, while `rag_recommend` appears later with shared setup assumptions that are easy to miss. | 1) env/client helpers, 2) compact request-builder helpers for each endpoint, 3) `rag_chat`, 4) `rag_recommend`, 5) optional shared response-parsing helpers at end. |
| `backend/app.py` | Route handlers are readable, but cache lifecycle helper (`load_graph_cache`) and commented-out write helper block appear far from routing intent and blur entrypoint scanning. | 1) app/bootstrap + blueprint registration, 2) cache lifecycle helpers (`load_graph_cache`, optional disabled write helper moved to end), 3) route handlers grouped by domain (`graph`, then `health`), 4) local run guard. |
| `backend/repopulation/loader.py` | Public API functions (`load_import_rows`, `graph_from_db`, publish helpers) are mixed with private upsert helpers, forcing cross-jumps while tracing run/publish logic. | 1) public APIs (`load_import_rows`, `graph_from_db`, `publish_run`, `get_published_run_id`), 2) publish internals (`_maybe_set_initial_published`), 3) upsert internals (`_get_or_create_run`, `_get_or_create_source`). |
| `backend/repopulation/importer/cache_to_rows.py` | Core converter appears after many small helper definitions, so top-down readers must skip ahead to find the main control flow. | 1) constants, 2) public entrypoint `cache_to_rows`, 3) row-construction helpers (`_node_row`, `_edge_row`, `_relevance_row`), 4) tiny utility helpers (`_as_list`, paper/run/source row builders). |
| `backend/repopulation/sources/openalex_parse.py` | Public parse functions and many private conversion helpers are mixed in a long file; related helper groups are not visually clustered by concern. | 1) dataclasses, 2) public parse API (`parse_openalex_author(s)`, `parse_openalex_work(s)`), 3) institution/topic/work extraction helpers, 4) generic scalar helpers (`_optional_*`, `_nested`, `_first`) at bottom. |

### Priority Order for Future Legibility Refactor

1. `backend/services/dynamodb_service.py` (highest scan-frequency in graph build path)
2. `backend/services/bedrock_service.py` (largest readability burden in diagnostics-heavy flow)
3. `backend/repopulation/loader.py` (key phase-1 data path and publish semantics)
4. `backend/app.py` (primary backend entrypoint)
5. `backend/repopulation/importer/cache_to_rows.py` and `backend/repopulation/sources/openalex_parse.py` (secondary but worthwhile for maintainability)

### Out-of-Scope in Task 4

- No method reordering performed.
- No comment movement performed.
- No behavior, signatures, imports, or module boundaries changed.

## Task 5: Read-Only TypeScript Structure Pass

Goal: identify TypeScript/React method/comment placement issues and propose non-behavioral reordering plans only.

### Candidate Reorder Plans (No Code Changes Applied)

| File | Current readability concern | Candidate declaration order |
|---|---|---|
| `src/components/ResearchNetworkGraph.tsx` | Very large component mixes state declarations, data-load logic, graph-engine setup, helper handlers, and render blocks; duplicated node-object creation logic appears in two effects. | 1) props/types, 2) constants and tiny pure helpers (node mesh/text builder), 3) state/hooks, 4) event handlers grouped by feature (hover, modal, search, recommendation, lab), 5) data-fetch effect, 6) graph initialization effect, 7) highlight update effect, 8) render states (loading/error/main UI). |
| `src/components/SearchBar.tsx` | Search, highlight, keyboard navigation, outside-click handling, and resume-upload logic are interleaved, making the control flow harder to scan. | 1) props/types, 2) state/refs, 3) pure search/highlight helpers, 4) input and keyboard handlers, 5) upload handlers, 6) lifecycle effects, 7) JSX render section. |
| `src/components/ResearcherModal.tsx` | Utility formatter (`formatContactInfo`) and async PDF workflow are separated by UI state logic; inline about truncation IIFE adds cognitive overhead mid-render. | 1) props/state, 2) close and async action handlers, 3) utility formatters, 4) small derived display values (about preview flags/text) before return, 5) JSX sections in display order. |
| `src/components/PaperChatModal.tsx` | Lifecycle effects and send/clear handlers are readable but message transformation, API call, and error handling are tightly coupled in one long handler. | 1) props/state/refs, 2) lifecycle effects, 3) message factory helpers (user/bot/error), 4) send handler using helpers, 5) input key handler/clear action, 6) guard clauses and render. |
| `src/components/VRGraph.tsx` | Initialization effect contains cleanup, positioning strategy, graph config, and logging in one block; status/error rendering is clear but setup path is dense. | 1) props/state/refs, 2) position-transform helper, 3) graph init helper, 4) effect that calls helpers + cleanup, 5) render branches (loading/no-data/error/main). |
| `src/services/dynamodb.ts` | Type contract and API calls are clear, but a legacy stub object at the bottom can distract from the primary functional API. | 1) type definitions, 2) primary fetch functions, 3) backward-compat exports (`DynamoDBService`) grouped under explicit legacy section comment. |
| `src/App.tsx` | Compact and clear, but fetch-in-effect helper can be moved into a named hook-like local function for top-down readability with route rendering. | 1) imports/types, 2) state, 3) named data-load callback, 4) effect invoking callback, 5) route render tree. |

### Comment Placement Observations (Task 5 Scope)

- Prefer section-header comments only at stable structural boundaries; large separator blocks in small files add visual noise.
- Inline comments that restate obvious JSX or assignment intent can be reduced in favor of concise function names.
- In very large component files, short comments should mark lifecycle/ownership boundaries (state, handlers, effects) rather than individual lines.

### Priority Order for Future TS Legibility Refactor

1. `src/components/ResearchNetworkGraph.tsx`
2. `src/components/SearchBar.tsx`
3. `src/components/ResearcherModal.tsx`
4. `src/components/PaperChatModal.tsx` and `src/components/VRGraph.tsx`
5. `src/services/dynamodb.ts` and `src/App.tsx`

### Out-of-Scope in Task 5

- No function/method reordering performed.
- No comment movement performed.
- No behavior, props contracts, or data flow changed.
