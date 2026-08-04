"""RSI mean reversion — buy oversold, exit once the bounce arrives.

The opposite premise to momentum: that short-term moves overshoot and snap
back. Both cannot be right about the same horizon, which is the point of
having them side by side under the same harness.

Stateful by design — it holds a position between the entry and exit
thresholds, so a single instance belongs to a single backtest run. The
walk-forward factory builds a fresh one per run.
"""

from __future__ import annotations

from ..features.indicators import rsi_last
from .base import History, Weights


class RsiMeanReversion:
    def __init__(
        self,
        symbol: str,
        window: int = 14,
        entry_below: float = 30.0,
        exit_above: float = 55.0,
    ) -> None:
        if entry_below >= exit_above:
            raise ValueError("entry threshold must sit below the exit threshold")
        self.symbol = symbol
        self.window = window
        self.entry_below = entry_below
        self.exit_above = exit_above
        self._long = False

    def on_bar(self, history: History) -> Weights:
        close = history[self.symbol]["close"]
        if len(close) < self.window + 2:
            return {}

        # Only the tail is needed. Recomputing the RSI over all history on
        # every bar makes a backtest O(n^2); 10x the window is far more
        # burn-in than the EWM needs (initial weight decays to ~5e-5).
        tail = close.to_numpy(dtype=float, copy=False)[-(self.window * 10) :]
        level = rsi_last(tail, self.window)

        if not self._long and level < self.entry_below:
            self._long = True
        elif self._long and level > self.exit_above:
            self._long = False

        return {self.symbol: 1.0 if self._long else 0.0}
