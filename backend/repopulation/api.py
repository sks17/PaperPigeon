"""New API service for the Repopulation Engine (FastAPI).

Phase-1 scope: serve `GET /api/graph/data` from Postgres with the SAME shape the existing Flask
endpoint returns, so the existing frontend renders off the new backend with zero code change.
The DB-backed path expands weighted COAUTHORED_WITH edges back to parallel paper links (see
loader.graph_from_db / SCHEMA.md), reproducing the existing graph.

Run locally:  DATABASE_URL=... uvicorn backend.repopulation.api:app --port 8000
(or use scripts/run_local_stack.py which boots the no-Docker Postgres + this API together).
The engine is created lazily on first request so importing this module never needs a live DB.
"""
from __future__ import annotations

import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.repopulation.db import make_engine, make_session_factory
from backend.repopulation.discovery_service import enqueue_discovery, job_status
from backend.repopulation.loader import graph_from_db
from backend.repopulation.reads import lab_detail, list_runs, node_description


class DiscoverRequest(BaseModel):
    """Body for POST /api/discover. Bounds reject oversized inputs before any DB/network work."""

    institution: str = Field(min_length=1, max_length=200)
    topic: str | None = Field(default=None, max_length=200)
    scrape: bool = False


def require_discovery_key(
    x_discovery_key: str | None = Header(default=None, alias="X-Discovery-Key"),
) -> None:
    """Gate the discovery endpoints behind DISCOVERY_API_KEY. Fail-closed: 401 when the secret is
    unset or the header doesn't match (constant-time compare)."""
    expected = os.getenv("DISCOVERY_API_KEY")
    if not expected or not x_discovery_key or not hmac.compare_digest(x_discovery_key, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")

_session_factory = None


def get_session_factory():
    """Lazy singleton session factory built from DATABASE_URL on first use."""
    global _session_factory
    if _session_factory is None:
        _session_factory = make_session_factory(make_engine())
    return _session_factory


def get_session():
    """FastAPI dependency yielding a DB session (overridable in tests)."""
    factory = get_session_factory()
    with factory() as session:
        yield session


def create_app() -> FastAPI:
    app = FastAPI(title="Paper Pigeon — Repopulation API")
    # Existing app is CORS-open ('*'); keep parity so the frontend works on any domain.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/api/graph/data")
    def graph_data(run: int | None = None, session: Session = Depends(get_session)) -> dict:
        """Byte-compatible replacement for the legacy GET /api/graph/data, served from Postgres.
        `?run=<id>` serves that repopulation run's snapshot; omitted serves the published run
        (the legacy graph by default), so existing behavior is unchanged."""
        return graph_from_db(session, run_id=run)

    # Node ids contain '/' and ':' (e.g. https://openalex.org/A1, lab:https://...), so detail reads
    # take the id as a query param rather than a path segment. Additive surface (Phase 4): the graph
    # node stays minimal; the enriched, grounded data is read here.
    @app.get("/api/node/description")
    def node_desc(id: str, session: Session = Depends(get_session)) -> dict:
        """A node's grounded `about` text + the evidence that grounds it."""
        detail = node_description(session, id)
        if detail is None:
            raise HTTPException(status_code=404, detail="node not found")
        return detail

    @app.get("/api/lab")
    def lab(id: str, session: Session = Depends(get_session)) -> dict:
        """A lab's enriched record (description, research areas, PI, url, resolved faculty)."""
        detail = lab_detail(session, id)
        if detail is None:
            raise HTTPException(status_code=404, detail="lab not found")
        return detail

    @app.get("/api/runs")
    def runs(session: Session = Depends(get_session)) -> list[dict]:
        """List repopulation runs (id, seed, status, published, counts) for the run-snapshot picker."""
        return list_runs(session)

    # ── on-demand discovery (key-gated) ───────────────────────────────────────
    # POST enqueues a job (or returns a cached run / the live job); the worker process runs the
    # pipeline. No network work happens in the request itself.
    @app.post("/api/discover")
    def discover(
        req: DiscoverRequest,
        session: Session = Depends(get_session),
        _key: None = Depends(require_discovery_key),
    ) -> dict:
        """Enqueue discovery of an institution/topic. Returns {job_id, run_id, status, cached}."""
        return enqueue_discovery(
            session, institution=req.institution, topic=req.topic, scrape=req.scrape
        )

    @app.get("/api/discover/{job_id}")
    def discover_status(
        job_id: int,
        session: Session = Depends(get_session),
        _key: None = Depends(require_discovery_key),
    ) -> dict:
        """Poll a discovery job's status/stage/run_id/error."""
        status = job_status(session, job_id)
        if status is None:
            raise HTTPException(status_code=404, detail="job not found")
        return status

    return app


app = create_app()
