"""Time-series momentum — long while the trailing return is positive.

The most-documented systematic strategy there is, and the one with the most
sobering evidence: in-sample Sharpes cluster around 0.1–0.2 and turn negative
out-of-sample for nearly all parameterisations. Included precisely because it
is the honest benchmark for "an idea everyone believes in" — run it through
walk-forward before believing anything about it.
"""

from __future__ import annotations

from .base import History, Weights


class TimeSeriesMomentum:
    """Fully invested while the past `lookback` bars produced a gain."""

    def __init__(self, symbol: str, lookback: int = 252) -> None:
        if lookback < 2:
            raise ValueError("lookback must be at least 2 bars")
        self.symbol = symbol
        self.lookback = lookback

    def on_bar(self, history: History) -> Weights:
        close = history[self.symbol]["close"]
        if len(close) < self.lookback + 1:
            return {}

        past_return = close.iloc[-1] / close.iloc[-self.lookback - 1] - 1.0
        return {self.symbol: 1.0 if past_return > 0 else 0.0}
