"""Indicators. Pure functions, and every value at time t uses only data <= t.

`rolling()` is backward-looking by default, which is what we want. Never reach
for `.shift(-n)` or `center=True` in this file.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average. NaN until `window` observations exist."""
    return series.rolling(window).mean()


def _ewm_last(values: np.ndarray, alpha: float) -> float:
    """Final value of `ewm(alpha=alpha, adjust=False)`, in closed form.

    The recursion ``y[n] = (1-a)y[n-1] + a x[n]`` seeded at ``y[0] = x[0]``
    expands to a weighted sum, so the whole series collapses to one dot
    product. Used on the strategy hot path, where calling pandas per bar turns
    a backtest quadratic.
    """
    if values.size == 0:
        return float("nan")
    if values.size == 1:
        return float(values[0])

    decay = 1.0 - alpha
    n = values.size - 1
    weights = decay ** np.arange(n - 1, -1, -1)
    return float(decay**n * values[0] + alpha * np.dot(weights, values[1:]))


def rsi_last(values: np.ndarray, window: int = 14) -> float:
    """RSI of the final bar only — same Wilder smoothing as `rsi`."""
    if values.size < 2:
        return 50.0

    delta = np.diff(values)
    alpha = 1.0 / window
    avg_gain = _ewm_last(np.clip(delta, 0.0, None), alpha)
    avg_loss = _ewm_last(-np.clip(delta, None, 0.0), alpha)

    if avg_loss <= 0:
        return 100.0
    return float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))


def realized_volatility(series: pd.Series, window: int = 20, periods_per_year: int = 252) -> pd.Series:
    """Annualised standard deviation of returns over a trailing window.

    The denominator in volatility targeting: position size scales inversely
    with this, so risk stays roughly constant instead of ballooning in a crisis.
    """
    return series.pct_change().rolling(window).std() * (periods_per_year**0.5)


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
