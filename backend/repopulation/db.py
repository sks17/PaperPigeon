"""Engine/session helpers for the Repopulation Engine (main-thread integration code).

Phase 1 uses a local Postgres (Docker was unavailable on the dev box, so we boot a no-Docker
local PG16+pgvector via `pgserver` — see scripts/phase1_local_db.py). DATABASE_URL / DATABASE_URL_RO
remain the future Render/Fly deployment variables; a prod URL will be provided later. The SQL
migration (migrations/0001_initial.sql) is the DDL of record — we do NOT use create_all().
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def migration_files() -> list[Path]:
    """SQL migrations in apply order (lexical: 0001_, 0002_, ...)."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def normalize_url(url: str) -> str:
    """Force the psycopg (v3) driver so a bare postgresql:// URI works with SQLAlchemy 2.0."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def make_engine(url: str | None = None) -> Engine:
    url = url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("No database URL: pass one or set DATABASE_URL.")
    return create_engine(normalize_url(url), future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, future=True)
