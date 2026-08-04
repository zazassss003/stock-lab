"""Indicators. Pure functions, and every value at time t uses only data <= t.

`rolling()` is backward-looking by default, which is what we want. Never reach
for `.shift(-n)` or `center=True` in this file.
"""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average. NaN until `window` observations exist."""
    return series.rolling(window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Relative strength index, 0-100, Wilder-smoothed."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False).mean()

    # A zero average loss means an unbroken run of gains: RSI is 100 there.
    rs = avg_gain / avg_loss.replace(0.0, float("nan"))
    return (100.0 - 100.0 / (1.0 + rs)).fillna(100.0)
