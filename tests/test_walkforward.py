"""Tests for the validation layer itself.

If the harness that judges strategies is wrong, every verdict it has issued is
wrong too — in either direction. These pin the parts that are easy to get
subtly, invisibly wrong.
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from stocklab.backtest.walkforward import (
    deflated_sharpe,
    expected_max_sharpe,
    slice_result,
    walk_forward,
)
from stocklab.strategy.sma_cross import SmaCross

from conftest import make_bars


GRID = [{"fast": 5, "slow": 20}, {"fast": 10, "slow": 30}, {"fast": 15, "slow": 40}]


def _walk(n_bars: int = 900, grid=GRID):
    # `grid` defaults directly rather than via `grid or GRID`: an empty grid is
    # falsy, and the fallback would quietly replace the case under test.
    bars = make_bars(n_bars=n_bars, symbols=("AAA",))
    return walk_forward(
        bars,
        lambda p: SmaCross("AAA", p["fast"], p["slow"]),
        grid,
        train_bars=400,
        test_bars=100,
    )


# ------------------------------------------------------- trial accounting


def test_trials_counts_every_evaluation_but_configs_counts_parameter_sets():
    """The distinction the audit turned up, pinned so it cannot regress."""
    result = _walk()

    assert result.trials == len(result.folds) * len(GRID)
    assert len(result.sharpes_by_config) == len(GRID)
    assert len(result.config_sharpes) == len(GRID)
    assert len(result.trial_sharpes) == result.trials


def test_counting_fewer_trials_never_lowers_the_deflated_sharpe():
    """Fewer searches means a lower bar — so the lenient number must not be
    stricter. If this ever inverts, the two are wired up backwards."""
    s = _walk().summary

    assert s["deflated_sharpe_by_config"] >= s["deflated_sharpe"] - 1e-9


def test_more_trials_raise_the_bar_a_result_must_clear():
    """The whole point of deflation: searching harder makes evidence weaker."""
    few = expected_max_sharpe([0.02, 0.05, 0.08])
    many = expected_max_sharpe([0.02, 0.05, 0.08] * 20)

    assert many > few


def test_deflated_sharpe_is_a_probability():
    for observed in (-0.1, 0.0, 0.05, 0.5):
        value = deflated_sharpe(observed, [0.01, 0.03, 0.05], n_observations=500)
        assert 0.0 <= value <= 1.0


def test_deflated_sharpe_degenerate_inputs_do_not_explode():
    assert deflated_sharpe(0.1, [0.05], n_observations=100) >= 0.0  # single trial
    assert deflated_sharpe(0.1, [], n_observations=100) >= 0.0  # no trials
    assert deflated_sharpe(0.1, [0.02, 0.04], n_observations=1) == 0.0  # no data


# ------------------------------------------------------- windowing


def test_train_and_test_windows_never_overlap():
    """A single shared bar between fitting and scoring invalidates everything."""
    result = _walk()

    for fold in result.folds:
        assert fold.train_end < fold.test_start

    for earlier, later in zip(result.folds, result.folds[1:]):
        assert earlier.test_end < later.test_start


def test_folds_advance_by_exactly_the_test_window():
    result = _walk()
    assert len(result.folds) == (900 - 400) // 100


def test_slice_result_scores_only_the_window():
    from stocklab.backtest.engine import run

    bars = make_bars(n_bars=300, symbols=("AAA",))
    full = run(bars, SmaCross("AAA", 5, 20))
    index = bars["AAA"].index

    part = slice_result(full, index[100], index[199])

    assert part.equity.index[0] == index[100]
    assert part.equity.index[-1] == index[199]
    assert all(index[100] <= f.ts <= index[199] for f in part.fills)


def test_out_of_sample_curve_covers_every_test_window():
    result = _walk()

    assert not result.oos_equity.empty
    assert result.oos_equity.index.is_monotonic_increasing
    assert not result.oos_equity.index.has_duplicates
    assert result.oos_equity.index[-1] <= result.folds[-1].test_end


def test_an_empty_grid_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        _walk(grid=[])


def test_too_little_history_yields_no_folds_rather_than_a_wrong_answer():
    result = _walk(n_bars=300)

    assert result.folds == []
    assert result.summary == {}
    assert "no folds" in result.verdict


# ------------------------------------------------------- verdicts


def test_a_strategy_with_no_out_of_sample_edge_is_rejected():
    result = _walk()
    result.folds = [
        type(f)(**{**f.__dict__, "test_sharpe": -0.5}) for f in result.folds
    ]

    assert "REJECT" in result.verdict
    assert "no out-of-sample edge" in result.verdict


def test_verdict_flags_disagreement_between_the_two_trial_counts():
    """A verdict resting on a debatable N should say so, not read as settled."""
    result = _walk()
    verdict = result.verdict

    s = result.summary
    straddles = s["deflated_sharpe"] < 0.95 <= s["deflated_sharpe_by_config"]
    assert ("BORDERLINE" in verdict) == straddles


def test_retention_is_not_reported_from_a_negative_baseline():
    """Dividing by a negative in-sample Sharpe produces a meaningless ratio."""
    result = _walk()
    result.folds = [
        type(f)(**{**f.__dict__, "train_sharpe": -1.0, "test_sharpe": -0.5})
        for f in result.folds
    ]

    assert result.summary["retention"] == 0.0


def test_annualisation_is_consistent_between_sharpe_measures():
    """oos_sharpe is annualised; the trial Sharpes feeding DSR are per-period.
    Mixing the two silently inflates the result by sqrt(252)."""
    result = _walk()
    returns = result.oos_equity.pct_change().dropna()
    per_period = returns.mean() / returns.std()

    assert result.summary["oos_sharpe"] == pytest.approx(per_period * math.sqrt(252), rel=1e-9)
    assert max(abs(s) for s in result.trial_sharpes) < 1.0  # per-period, not annual
