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

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from backend.repopulation.db import make_engine, make_session_factory
from backend.repopulation.loader import graph_from_db

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
    def graph_data(session: Session = Depends(get_session)) -> dict:
        """Byte-compatible replacement for the legacy GET /api/graph/data, served from Postgres."""
        return graph_from_db(session)

    return app


app = create_app()
