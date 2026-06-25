"""Daily spend budget for paid APIs (main-thread cost guard).

Enforces a per-day USD ceiling (PAPERPIGEON_BUDGET_PRO_DAILY_USD) across OpenAlex list calls and
OpenRouter embeddings, so a runaway sweep can't blow the bill. Spend is persisted to a small
day-keyed ledger so re-runs within the same day accumulate (true "daily" semantics). `today` is
passed in by the caller (the pure-code wall-clock ban doesn't apply to client code, but threading
it keeps this testable/deterministic).

Cost model (validated 2026 pricing):
  - OpenAlex list+filter request  ≈ $0.10 / 1k  → $0.0001 / request  (single-entity lookups are $0)
  - OpenRouter text-embedding-3-small ≈ $0.02 / 1M tokens (~chars/4 tokens estimate)
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

OPENALEX_LIST_COST = 0.10 / 1000.0
EMBED_USD_PER_1M_TOKENS = 0.02
# Cheap extraction model (Gemini Flash via OpenRouter) — conservative blended in/out estimate.
CHAT_USD_PER_1M_TOKENS = 0.40


class BudgetExceeded(RuntimeError):
    pass


class DailyBudget:
    def __init__(self, cap_usd: float | None, ledger_path: str | Path, today: str) -> None:
        self.cap = cap_usd
        self.today = today
        self._path = Path(ledger_path)
        self.spent = self._load()

    def _load(self) -> float:
        if self._path.exists():
            return float(json.loads(self._path.read_text(encoding="utf-8")).get(self.today, 0.0))
        return 0.0

    def _save(self) -> None:
        ledger = {}
        if self._path.exists():
            ledger = json.loads(self._path.read_text(encoding="utf-8"))
        ledger[self.today] = round(self.spent, 6)
        self._path.write_text(json.dumps(ledger), encoding="utf-8")

    def charge(self, usd: float, what: str = "") -> None:
        """Reserve `usd` of spend; raise BudgetExceeded (before the API call) if it would exceed."""
        if usd <= 0:
            return
        if self.cap is not None and self.spent + usd > self.cap:
            raise BudgetExceeded(
                f"daily budget ${self.cap:.2f} would be exceeded: spent ${self.spent:.4f} "
                f"+ ${usd:.4f} for {what} (day {self.today})"
            )
        self.spent += usd
        self._save()

    def remaining(self) -> float | None:
        return None if self.cap is None else max(0.0, self.cap - self.spent)


def estimate_embed_cost(texts: list[str]) -> float:
    tokens = sum(len(t) for t in texts) / 4.0  # ~4 chars/token
    return tokens / 1_000_000.0 * EMBED_USD_PER_1M_TOKENS


def estimate_chat_cost(prompt_chars: int, *, max_output_chars: int = 2000) -> float:
    tokens = (prompt_chars + max_output_chars) / 4.0
    return tokens / 1_000_000.0 * CHAT_USD_PER_1M_TOKENS


# DB-backed variant of DailyBudget for the deployed worker. fly's filesystem is ephemeral and
# per-machine, so the file ledger above can't enforce a real daily cap across restarts/workers; this
# keeps the spend in `repop.budget_ledger` and charges atomically. Same .charge()/.spent/.remaining()
# interface, so OpenAlex/Embeddings/LLM clients accept it unchanged. Uses raw SQL (no ORM import) to
# keep this module light. `today` is a datetime.date (worker may read the clock).
class DbDailyBudget:
    # Ensure the day row exists at 0, then guard the increment against the cap in one atomic UPDATE.
    _ENSURE = text(
        "INSERT INTO repop.budget_ledger (day, spent_usd) VALUES (:day, 0) "
        "ON CONFLICT (day) DO NOTHING"
    )
    _BUMP = text("UPDATE repop.budget_ledger SET spent_usd = spent_usd + :usd WHERE day = :day")
    _CHARGE = text(
        "UPDATE repop.budget_ledger SET spent_usd = spent_usd + :usd "
        "WHERE day = :day AND spent_usd + :usd <= :cap "
        "RETURNING spent_usd"
    )
    _SPENT = text("SELECT spent_usd FROM repop.budget_ledger WHERE day = :day")

    def __init__(self, session_factory, cap_usd, today) -> None:
        self._sf = session_factory
        self.cap = cap_usd
        self.today = today

    def charge(self, usd: float, what: str = "") -> None:
        """Atomically reserve `usd`; raise BudgetExceeded (before the API call) if it would exceed."""
        if usd <= 0:
            return
        with self._sf() as session:
            session.execute(self._ENSURE, {"day": self.today})
            if self.cap is None:  # no cap → record spend, never block
                session.execute(self._BUMP, {"usd": usd, "day": self.today})
                session.commit()
                return
            row = session.execute(
                self._CHARGE, {"usd": usd, "day": self.today, "cap": self.cap}
            ).first()
            session.commit()
        if row is None:  # the guarded UPDATE matched nothing -> the cap would be exceeded
            raise BudgetExceeded(
                f"daily budget ${self.cap:.2f} reached: + ${usd:.4f} for {what} (day {self.today})"
            )

    @property
    def spent(self) -> float:
        with self._sf() as session:
            value = session.execute(self._SPENT, {"day": self.today}).scalar()
        return float(value or 0.0)

    def remaining(self) -> float | None:
        return None if self.cap is None else max(0.0, self.cap - self.spent)
