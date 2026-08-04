"""Equal-weight buy and hold — the benchmark every idea must beat.

Deliberately trivial. If a clever strategy cannot beat this after costs, the
clever strategy is not worth running.
"""

from __future__ import annotations

from .base import History, Weights


class BuyAndHold:
    def on_bar(self, history: History) -> Weights:
        weight = 1.0 / len(history)
        return {symbol: weight for symbol in history}
