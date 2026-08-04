from __future__ import annotations

import pandas as pd
import pytest

from stocklab.features.indicators import rsi, sma


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


def test_rsi_bounds_and_direction():
    rising = pd.Series(range(1, 40), dtype=float)
    falling = pd.Series(range(40, 1, -1), dtype=float)

    assert rsi(rising).iloc[-1] > 90.0
    assert rsi(falling).iloc[-1] < 10.0
    assert rsi(rising).between(0.0, 100.0).all()
