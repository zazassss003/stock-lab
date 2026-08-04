"""The one path from raw data to a rendered dashboard.

Both the manual script and the scheduled daily job call this, so there is no
second copy of the wiring to drift out of sync.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .backtest import engine
from .data.yfinance_source import YFinanceSource
from .report.dashboard import build_payload, render_dashboard
from .strategy.buy_and_hold import BuyAndHold
from .strategy.sma_cross import SmaCross

# A US session that closed more than this many days ago means the feed, the
# network, or the schedule is broken — not that the market was quiet.
# Long weekends and holidays make three days normal; five is not.
STALE_AFTER_DAYS = 5

# Anchored to the repo, not the working directory: a scheduled run launches
# from wherever the scheduler chooses, and a relative cache path would quietly
# start a second, empty cache there.
REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data_store"


def refresh_and_report(symbol: str = "SPY", output: str | Path = "dashboard.html") -> dict:
    """Pull the latest bars, re-run the strategies, rewrite the dashboard."""
    source = YFinanceSource(cache_dir=CACHE_DIR)
    bars = source.fetch([symbol])

    results = {
        "buy_hold": ("Buy & hold", engine.run(bars, BuyAndHold())),
        "sma_cross": ("SMA 20/50", engine.run(bars, SmaCross(symbol, 20, 50))),
    }

    payload = build_payload(symbol, bars, results)
    path = render_dashboard(payload, output)

    last_bar = bars[symbol].index[-1]
    age = (pd.Timestamp.now(tz="UTC").normalize() - last_bar.normalize()).days

    return {
        "symbol": symbol,
        "path": path,
        "bars": len(bars[symbol]),
        "first_bar": bars[symbol].index[0].date(),
        "last_bar": last_bar.date(),
        "age_days": age,
        "stale": age > STALE_AFTER_DAYS,
        # Sessions the provider returned without usable prices. Usually the
        # newest bar, resolving itself on a later run — but if a date persists
        # here across runs, the feed is wrong and the charts are missing a day.
        "unpriced": [ts.date() for ts in getattr(source, "dropped_unpriced", {}).get(symbol, [])],
        "results": results,
    }
