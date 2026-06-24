"""Tests for the daily spend budget guard (clients/budget.py)."""
from __future__ import annotations

import json

import pytest

from backend.repopulation.clients.budget import (
    BudgetExceeded,
    DailyBudget,
    estimate_embed_cost,
)


def test_charge_accumulates_and_persists(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    b = DailyBudget(1.0, ledger, "2026-06-24")
    b.charge(0.3, "a")
    b.charge(0.2, "b")
    assert b.spent == pytest.approx(0.5)
    # Persisted day-keyed; a fresh instance for the same day resumes the spend.
    assert json.loads(ledger.read_text())["2026-06-24"] == pytest.approx(0.5)
    assert DailyBudget(1.0, ledger, "2026-06-24").spent == pytest.approx(0.5)


def test_charge_raises_before_exceeding_cap(tmp_path) -> None:
    b = DailyBudget(0.001, tmp_path / "l.json", "2026-06-24")
    b.charge(0.0008, "ok")
    with pytest.raises(BudgetExceeded):
        b.charge(0.0005, "over")
    # The rejected charge is NOT applied.
    assert b.spent == pytest.approx(0.0008)


def test_new_day_starts_fresh(tmp_path) -> None:
    ledger = tmp_path / "l.json"
    DailyBudget(1.0, ledger, "2026-06-24").charge(0.7, "day1")
    assert DailyBudget(1.0, ledger, "2026-06-25").spent == 0.0


def test_no_cap_never_raises(tmp_path) -> None:
    b = DailyBudget(None, tmp_path / "l.json", "2026-06-24")
    b.charge(1_000.0, "huge")
    assert b.remaining() is None


def test_zero_charge_is_noop(tmp_path) -> None:
    b = DailyBudget(1.0, tmp_path / "l.json", "2026-06-24")
    b.charge(0.0, "free")
    assert b.spent == 0.0


def test_estimate_embed_cost_scales_with_text() -> None:
    assert estimate_embed_cost([]) == 0.0
    assert estimate_embed_cost(["x" * 4000]) > estimate_embed_cost(["x" * 40])
