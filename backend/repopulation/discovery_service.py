"""Discovery-service helpers: normalize a seed, enqueue/dedup a discovery job, read job status.

Backs POST /api/discover and GET /api/discover/{id}. Dedup is by `seed_hash` (normalized
institution+topic, lowercased; the scrape flag is excluded — scraping only adds lab nodes to the
same seed/run):
  - a prior SUCCEEDED job for this seed (with adequate scrape) → return its run as a free cache hit;
  - a live (queued/running) job for this seed → return it (the partial unique index also blocks a
    duplicate insert under a race);
  - otherwise enqueue a new queued job for the worker.

The endpoint stays thin (validation + auth only); all the DB logic lives here so it's unit-testable.
"""
from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.repopulation.models.discovery_job import (
    JOB_QUEUED,
    JOB_SUCCEEDED,
    LIVE_STATUSES,
    DiscoveryJob,
)


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def seed_hash(institution: str, topic: str | None) -> str:
    """Identity hash of a discovery request (case/whitespace-insensitive; scrape excluded)."""
    base = normalize_text(institution).lower() + "\x00" + normalize_text(topic).lower()
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def build_seed(institution: str, topic: str | None) -> dict:
    """The seed dict stored on the job and fed to run_repopulation."""
    return {
        "institution": normalize_text(institution),
        "topic": normalize_text(topic) or None,
        "keywords": [],
    }


def _payload(job: DiscoveryJob, *, cached: bool) -> dict:
    return {"job_id": job.id, "run_id": job.run_id, "status": job.status, "cached": cached}


def enqueue_discovery(session: Session, *, institution: str, topic: str | None, scrape: bool) -> dict:
    """Dedup + enqueue. Returns {job_id, run_id, status, cached}."""
    digest = seed_hash(institution, topic)

    # Cache hit: a previously succeeded job for this seed whose scrape coverage is adequate.
    cache_conds = [
        DiscoveryJob.seed_hash == digest,
        DiscoveryJob.status == JOB_SUCCEEDED,
        DiscoveryJob.run_id.isnot(None),
    ]
    if scrape:  # a scrape request can only reuse a run that was itself scraped
        cache_conds.append(DiscoveryJob.scrape.is_(True))
    cached = session.scalar(
        select(DiscoveryJob).where(*cache_conds).order_by(DiscoveryJob.id.desc())
    )
    if cached is not None:
        return _payload(cached, cached=True)

    # A live job already covers this seed → return it.
    live = session.scalar(
        select(DiscoveryJob).where(
            DiscoveryJob.seed_hash == digest, DiscoveryJob.status.in_(LIVE_STATUSES)
        )
    )
    if live is not None:
        return _payload(live, cached=False)

    job = DiscoveryJob(
        seed=build_seed(institution, topic), seed_hash=digest, scrape=bool(scrape),
        status=JOB_QUEUED, stage=JOB_QUEUED,
    )
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        # Lost a race on the live-seed unique index — return the winner.
        session.rollback()
        live = session.scalar(
            select(DiscoveryJob).where(
                DiscoveryJob.seed_hash == digest, DiscoveryJob.status.in_(LIVE_STATUSES)
            )
        )
        if live is not None:
            return _payload(live, cached=False)
        raise
    return _payload(job, cached=False)


def job_status(session: Session, job_id: int) -> dict | None:
    job = session.get(DiscoveryJob, job_id)
    if job is None:
        return None
    return {
        "id": job.id,
        "status": job.status,
        "stage": job.stage,
        "run_id": job.run_id,
        "scrape": job.scrape,
        "error": job.error,
        "requested_at": job.requested_at.isoformat() if job.requested_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
