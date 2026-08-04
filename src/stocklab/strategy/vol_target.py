"""Momentum sized by inverse volatility rather than all-or-nothing.

Volatility targeting is the one refinement the momentum literature agrees on:
it raises Sharpe, cuts tail returns and shrinks drawdowns — not by predicting
anything, but by holding risk constant instead of letting it balloon exactly
when markets get violent.

Position = momentum signal x (target vol / realised vol), capped at fully
invested. No leverage, so in calm markets it simply sits at 100%.
"""

from __future__ import annotations

from ..features.indicators import realized_volatility
from .base import History, Weights


class VolatilityTargetedMomentum:
    def __init__(
        self,
        symbol: str,
        lookback: int = 252,
        vol_window: int = 20,
        target_vol: float = 0.15,
    ) -> None:
        self.symbol = symbol
        self.lookback = lookback
        self.vol_window = vol_window
        self.target_vol = target_vol

    def on_bar(self, history: History) -> Weights:
        close = history[self.symbol]["close"]
        if len(close) < max(self.lookback + 1, self.vol_window + 2):
            return {}

        if close.iloc[-1] / close.iloc[-self.lookback - 1] - 1.0 <= 0:
            return {self.symbol: 0.0}

        # Tail only — a rolling std over all history on every bar is O(n^2).
        vol = realized_volatility(close.iloc[-(self.vol_window + 2) :], self.vol_window).iloc[-1]
        if not vol or vol <= 0:
            return {self.symbol: 0.0}

        # Capped at 1.0: the engine rejects leverage, and this makes the cap
        # explicit rather than letting a quiet market raise a ValueError.
        return {self.symbol: float(min(1.0, self.target_vol / vol))}
