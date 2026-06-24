"""P2-T04: relevance scoring math.

Exercises the pure transforms in backend/repopulation/relevance/score.py against the contract in
DISCOVERY.md (§Relevance) and SCHEMA.md §2:

    relevance = w_cosine*cosine + w_recency*recency_decay(last_year) + w_volume*normalized_log1p(volume)

All inputs are inline fixtures; `current_year` is always passed in (no wall-clock). No DB / network.
Run by the main thread (`python -m pytest -q` from the project root).
"""
from __future__ import annotations

import math
from numbers import Real

import pytest

from backend.repopulation.relevance.score import (
    DEFAULT_HALFLIFE_YEARS,
    DEFAULT_WEIGHTS,
    cosine,
    recency_decay,
    score_relevance,
)

CURRENT_YEAR = 2024
RUN_KEY = "run-test"


# --- cosine -----------------------------------------------------------------

def test_cosine_identical_vectors_is_one() -> None:
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_parallel_vectors_is_one() -> None:
    # Direction, not magnitude, drives cosine.
    assert cosine([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_vectors_is_zero() -> None:
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_vectors_is_negative_one() -> None:
    assert cosine([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_is_safe() -> None:
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert cosine([1.0, 1.0], [0.0, 0.0]) == 0.0


def test_cosine_empty_vectors_is_safe() -> None:
    assert cosine([], []) == 0.0


# --- recency_decay ----------------------------------------------------------

def test_recency_decay_current_year_is_one() -> None:
    assert recency_decay(CURRENT_YEAR, CURRENT_YEAR) == pytest.approx(1.0)


def test_recency_decay_unknown_year_is_zero() -> None:
    assert recency_decay(None, CURRENT_YEAR) == 0.0


def test_recency_decay_decreases_with_age() -> None:
    recent = recency_decay(2022, CURRENT_YEAR)
    middling = recency_decay(2016, CURRENT_YEAR)
    old = recency_decay(2004, CURRENT_YEAR)
    assert 1.0 >= recent > middling > old > 0.0


def test_recency_decay_matches_halflife_formula() -> None:
    year = 2019
    expected = math.exp(-(CURRENT_YEAR - year) / DEFAULT_HALFLIFE_YEARS)
    assert recency_decay(year, CURRENT_YEAR) == pytest.approx(expected)


# --- score_relevance --------------------------------------------------------

def _fixture():
    seed_embedding = [1.0, 0.0]
    node_vectors = {
        "n_match": [1.0, 0.0],        # cosine 1.0
        "n_orthogonal": [0.0, 1.0],   # cosine 0.0
    }
    node_meta = {
        "n_match": {"last_year": CURRENT_YEAR, "volume": 10},
        "n_orthogonal": {"last_year": 2010, "volume": 0},
    }
    return seed_embedding, node_vectors, node_meta


def test_score_relevance_one_row_per_node() -> None:
    seed, vectors, meta = _fixture()
    rows = score_relevance(seed, vectors, meta, RUN_KEY, CURRENT_YEAR)
    assert len(rows) == len(vectors)
    assert {row["node_id"] for row in rows} == set(vectors)


def test_score_relevance_row_shape_and_run_scope() -> None:
    seed, vectors, meta = _fixture()
    rows = score_relevance(seed, vectors, meta, RUN_KEY, CURRENT_YEAR)
    for row in rows:
        assert set(row) >= {"node_id", "run_key", "score", "components"}
        assert row["run_key"] == RUN_KEY
        assert isinstance(row["score"], Real) and not isinstance(row["score"], bool)
        assert isinstance(row["components"], dict)


def test_score_in_sane_range() -> None:
    seed, vectors, meta = _fixture()
    rows = score_relevance(seed, vectors, meta, RUN_KEY, CURRENT_YEAR)
    for row in rows:
        assert math.isfinite(row["score"])
        assert row["score"] >= 0.0  # all components are non-negative for this fixture


def test_components_sum_per_weights_to_score() -> None:
    seed, vectors, meta = _fixture()
    rows = score_relevance(seed, vectors, meta, RUN_KEY, CURRENT_YEAR)
    for row in rows:
        c = row["components"]
        weighted_sum = (
            DEFAULT_WEIGHTS["cosine"] * c["cosine"]
            + DEFAULT_WEIGHTS["recency"] * c["recency"]
            + DEFAULT_WEIGHTS["volume"] * c["volume"]
        )
        assert weighted_sum == pytest.approx(row["score"])


def test_volume_component_is_batch_normalized_and_keeps_raw_value() -> None:
    seed, vectors, meta = _fixture()
    rows = score_relevance(seed, vectors, meta, RUN_KEY, CURRENT_YEAR)
    by_id = {row["node_id"]: row for row in rows}

    assert by_id["n_match"]["components"]["volume"] == pytest.approx(1.0)
    assert by_id["n_match"]["components"]["volume_raw"] == pytest.approx(math.log1p(10))
    assert by_id["n_orthogonal"]["components"]["volume"] == pytest.approx(0.0)
    assert by_id["n_orthogonal"]["components"]["volume_raw"] == pytest.approx(0.0)


def test_volume_component_is_zero_when_batch_max_is_zero() -> None:
    rows = score_relevance(
        seed_embedding=[1.0, 0.0],
        node_vectors={"n_zero": [1.0, 0.0]},
        node_meta={"n_zero": {"last_year": CURRENT_YEAR, "volume": 0}},
        run_key=RUN_KEY,
        current_year=CURRENT_YEAR,
    )

    assert rows[0]["components"]["volume"] == 0.0
    assert rows[0]["components"]["volume_raw"] == 0.0


def test_score_matches_formula_for_known_inputs() -> None:
    seed, vectors, meta = _fixture()
    rows = score_relevance(seed, vectors, meta, RUN_KEY, CURRENT_YEAR)
    by_id = {row["node_id"]: row for row in rows}

    w = DEFAULT_WEIGHTS
    expected_match = (
        w["cosine"] * 1.0
        + w["recency"] * 1.0
        + w["volume"] * 1.0
    )
    expected_orthogonal = (
        w["cosine"] * 0.0
        + w["recency"] * math.exp(-(CURRENT_YEAR - 2010) / DEFAULT_HALFLIFE_YEARS)
        + w["volume"] * 0.0
    )
    assert by_id["n_match"]["score"] == pytest.approx(expected_match)
    assert by_id["n_orthogonal"]["score"] == pytest.approx(expected_orthogonal)


def test_more_relevant_node_scores_higher() -> None:
    seed, vectors, meta = _fixture()
    rows = score_relevance(seed, vectors, meta, RUN_KEY, CURRENT_YEAR)
    by_id = {row["node_id"]: row for row in rows}
    assert by_id["n_match"]["score"] > by_id["n_orthogonal"]["score"]
