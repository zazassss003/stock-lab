"""Moving-average crossover — the textbook starter strategy.

Included as a worked example of the Strategy contract, not as an edge. It is
one of the most-tested ideas in existence; assume it is priced in and judge it
against buy-and-hold *after costs*.
"""

from __future__ import annotations

from .base import History, Weights


class SmaCross:
    """Long while the fast average is above the slow one, otherwise flat."""

    def __init__(self, symbol: str, fast: int = 20, slow: int = 50) -> None:
        if fast >= slow:
            raise ValueError("fast window must be shorter than slow")
        self.symbol = symbol
        self.fast = fast
        self.slow = slow

    def on_bar(self, history: History) -> Weights:
        close = history[self.symbol]["close"]

        # Not enough history to form the slow average yet — stay flat.
        if len(close) < self.slow:
            return {}

        fast_avg = close.iloc[-self.fast :].mean()
        slow_avg = close.iloc[-self.slow :].mean()
        return {self.symbol: 1.0 if fast_avg > slow_avg else 0.0}
