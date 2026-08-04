from __future__ import annotations

import pandas as pd
import pytest

import numpy as np

from stocklab.features.indicators import realized_volatility, rsi, rsi_last, sma


def test_sma_matches_hand_calculation():
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    result = sma(series, 3)

    assert result.iloc[:2].isna().all()  # not enough history yet
    assert result.iloc[2] == pytest.approx(2.0)
    assert result.iloc[4] == pytest.approx(4.0)


def test_sma_value_at_t_ignores_everything_after_t():
    """Truncating the future must not change a past value."""
    series = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0, 100.0])

    full = sma(series, 3)
    truncated = sma(series.iloc[:4], 3)

    pd.testing.assert_series_equal(full.iloc[:4], truncated)


def test_rsi_last_matches_the_pandas_reference():
    """The fast path is only worth having if it is the same number."""
    rng = np.random.default_rng(7)
    series = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.012, 300))))

    for window in (7, 14, 21):
        assert rsi_last(series.to_numpy(), window) == pytest.approx(
            rsi(series, window).iloc[-1], rel=1e-9
        )


def test_rsi_last_handles_degenerate_input():
    assert rsi_last(np.array([100.0]), 14) == 50.0
    assert rsi_last(np.array([1.0, 2.0, 3.0, 4.0]), 14) == 100.0  # no losses


def test_realized_volatility_scales_with_noise():
    rng = np.random.default_rng(3)
    calm = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.002, 200))))
    wild = pd.Series(100.0 * np.exp(np.cumsum(rng.normal(0, 0.020, 200))))

    assert realized_volatility(wild, 20).iloc[-1] > 5 * realized_volatility(calm, 20).iloc[-1]


def test_rsi_bounds_and_direction():
    rising = pd.Series(range(1, 40), dtype=float)
    falling = pd.Series(range(40, 1, -1), dtype=float)

    assert rsi(rising).iloc[-1] > 90.0
    assert rsi(falling).iloc[-1] < 10.0
    assert rsi(rising).between(0.0, 100.0).all()
