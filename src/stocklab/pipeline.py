"""The one path from raw data to a rendered dashboard.

Both the manual script and the scheduled daily job call this, so there is no
second copy of the wiring to drift out of sync.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .backtest import engine
from .backtest.costs import FLAT_DEFAULT
from .data.intraday import IntradaySource, last_quote
from .data.yfinance_source import YFinanceSource
from .report.dashboard import build_payload, build_symbol_block, render_dashboard
from .strategy.buy_and_hold import BuyAndHold
from .strategy.sma_cross import SmaCross
from .universe import SYMBOLS

# A US session that closed more than this many days ago means the feed, the
# network, or the schedule is broken — not that the market was quiet.
# Long weekends and holidays make three days normal; five is not.
STALE_AFTER_DAYS = 5

# Ignore drift below 2% of equity. Shared with scripts/research.py so the
# dashboard and the validation sweep describe the same system.
REBALANCE_BAND = 0.02

# Broker-neutral costs on the dashboard, on purpose. The page compares
# strategies against each other; pricing one specific account would let the
# broker's minimum ticket decide which strategy looks better. Use a broker
# preset from backtest/costs.py when the question is "what would this account
# have returned", not "which rule is better".
COSTS = FLAT_DEFAULT

# Anchored to the repo, not the working directory: a scheduled run launches
# from wherever the scheduler chooses, and a relative cache path would quietly
# start a second, empty cache there.
REPO_ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = REPO_ROOT / "data_store"
INTRADAY_CACHE_DIR = CACHE_DIR / "intraday"

# 1m feeds the 60-minute view, 5m feeds the 24-hour view. 60m is not fetched:
# nothing on the page asks for it, and each interval is 11 more downloads.
INTRADAY_INTERVALS = ("1m", "5m")


def refresh_and_report(
    symbols: list[str] | None = None,
    output: str | Path = "dashboard.html",
    intraday: bool = True,
) -> dict:
    """Pull the latest bars for every symbol and rewrite the dashboard."""
    symbols = symbols or SYMBOLS
    source = YFinanceSource(cache_dir=CACHE_DIR)
    run_kwargs = {"rebalance_band": REBALANCE_BAND, "costs": COSTS}

    # Intraday is presentation only — the sub-daily ranges on the dashboard.
    # It never reaches the engine; see data/intraday.py for why.
    intraday_source = IntradaySource(cache_dir=INTRADAY_CACHE_DIR) if intraday else None
    intraday_bars: dict[str, dict] = {}
    if intraday_source:
        for interval in INTRADAY_INTERVALS:
            for symbol, frame in intraday_source.fetch(symbols, interval).items():
                intraday_bars.setdefault(symbol, {})[interval] = frame

    blocks: dict[str, dict] = {}
    failures: dict[str, str] = {}
    unpriced: dict[str, list] = {}

    for symbol in symbols:
        # Fetched one at a time on purpose. A shared fetch intersects the
        # calendars, so one short-history name would silently truncate every
        # other symbol back to its own IPO date.
        try:
            bars = source.fetch([symbol])
        except Exception as error:  # a dead ticker must not sink the whole run
            failures[symbol] = str(error)
            continue

        results = {
            "buy_hold": ("Buy & hold", engine.run(bars, BuyAndHold(), **run_kwargs)),
            "sma_cross": ("SMA 20/50", engine.run(bars, SmaCross(symbol, 20, 50), **run_kwargs)),
        }
        quote = None
        if intraday_source:
            try:
                quote = last_quote(symbol)
            except Exception:
                quote = None  # a missing live quote is cosmetic, never fatal

        blocks[symbol] = build_symbol_block(
            symbol,
            bars[symbol],
            results,
            intraday=intraday_bars.get(symbol),
            quote=quote,
        )

        dropped = source.dropped_unpriced.get(symbol, [])
        if dropped:
            unpriced[symbol] = [ts.date() for ts in dropped]

    if not blocks:
        raise RuntimeError(f"no symbols could be fetched: {failures}")

    path = render_dashboard(build_payload(blocks, symbols, costs=COSTS.describe()), output)

    today = pd.Timestamp.now(tz="UTC").normalize()
    ages = {
        symbol: (today - pd.Timestamp(block["end"], tz="UTC")).days
        for symbol, block in blocks.items()
    }
    worst_age = max(ages.values())

    return {
        "symbols": list(blocks),
        "path": path,
        "bars": {s: len(b["dates"]) for s, b in blocks.items()},
        "last_bar": {s: b["end"] for s, b in blocks.items()},
        "ages": ages,
        "age_days": worst_age,
        "stale": worst_age > STALE_AFTER_DAYS,
        "stale_symbols": [s for s, age in ages.items() if age > STALE_AFTER_DAYS],
        # Sessions the provider returned without usable prices. Usually the
        # newest bar, resolving itself on a later run — but if a date persists
        # here across runs, the feed is wrong and the charts are missing a day.
        "unpriced": unpriced,
        "failures": failures,
    }
