"""The Strategy contract.

The signature is the whole safety mechanism: a strategy receives *history*
(bars up to and including now) and returns *intent* (target weights). It never
sees a future bar, and it never places an order itself.
"""

from __future__ import annotations

from typing import Mapping, Protocol

import pandas as pd

History = Mapping[str, pd.DataFrame]
Weights = Mapping[str, float]


class Strategy(Protocol):
    def on_bar(self, history: History) -> Weights:
        """Return target portfolio weights, e.g. ``{"SPY": 0.5}``.

        `history[symbol]` ends at the current bar — its last row is the bar
        just closed. Orders derived from this run at the *next* bar's open.

        Weights are fractions of current equity. They must satisfy
        ``sum(abs(w)) <= 1.0``: no leverage, no borrowing. A symbol left out is
        treated as weight 0, i.e. flat.
        """
        ...
