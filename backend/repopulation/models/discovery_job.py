"""Discovery-job + budget-ledger ORM models (migration 0004).

`DiscoveryJob` is the async work item for the on-demand discovery service: a POST enqueues one, the
worker claims it (FOR UPDATE SKIP LOCKED), runs the pipeline, and records status/stage/error/run_id.
`BudgetLedger` mirrors `repop.budget_ledger` — the DB-backed daily spend used by `DbDailyBudget`
(clients/budget.py) so the cap holds across worker restarts/machines. Mirrors 0004_discovery_job.sql.
"""
from __future__ import annotations

import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.repopulation.models.nodes import Base

# Job lifecycle (mirrors the CHECK in 0004): queued -> running -> succeeded | failed.
JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_SUCCEEDED = "succeeded"
JOB_FAILED = "failed"
LIVE_STATUSES = (JOB_QUEUED, JOB_RUNNING)


class DiscoveryJob(Base):
    """Mirror of `repop.discovery_job` — one on-demand discovery request."""

    __tablename__ = "discovery_job"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    seed: Mapped[dict] = mapped_column(JSONB, nullable=False)
    seed_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scrape: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    run_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("repop.repopulation_run.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    worker_id: Mapped[str | None] = mapped_column(Text)
    requested_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','succeeded','failed')",
            name="discovery_job_status_check",
        ),
        Index(
            "discovery_job_live_seed_uq",
            "seed_hash",
            unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
        Index("discovery_job_status_idx", "status"),
    )


class BudgetLedger(Base):
    """Mirror of `repop.budget_ledger` — DB-backed daily spend for DbDailyBudget."""

    __tablename__ = "budget_ledger"

    day: Mapped[datetime.date] = mapped_column(Date, primary_key=True)
    spent_usd: Mapped[float] = mapped_column(Double, nullable=False, server_default=text("0"))
