"""Deterministic market facts for one symbol, computed from cached bars.

This is the half of the briefing that does not involve a language model. Given
the same parquet files it produces the same numbers, so when a research note and
the context strip disagree, the context strip is right.

Every value here is backward-looking: the last bar and history before it. The
briefing reports on the past, it does not forecast, so there is no fill or
signal timing to get wrong — but the indicator functions are imported from
`features/` rather than reimplemented, so the page and the backtest can never
quietly compute a different RSI.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Mapping

import pandas as pd

from ..features.indicators import realized_volatility, rsi, sma
from ..universe import BY_SYMBOL

BARS_PER_MONTH = 21
BARS_PER_QUARTER = 63
BARS_PER_YEAR = 252


def _pct_change(close: pd.Series, bars: int) -> float | None:
    """Return over the last `bars` sessions, or None when history is short."""
    if len(close) <= bars:
        return None
    return float(close.iloc[-1] / close.iloc[-1 - bars] - 1.0)


def _finite(value: float) -> float | None:
    number = float(value)
    return None if math.isnan(number) else number


@dataclass(frozen=True)
class Context:
    """The numeric strip shown above every research note."""

    symbol: str
    name: str
    sector: str
    last_bar: str
    close: float
    chg_1d: float | None
    chg_1m: float | None
    chg_3m: float | None
    chg_1y: float | None
    # Relative to SPY over the same window. The only number that answers "was
    # owning this instead of the index worth it", which is the question the
    # universe docstring insists on asking.
    excess_1m: float | None
    excess_1y: float | None
    high_52w: float | None
    low_52w: float | None
    from_high: float | None
    rsi: float | None
    trend: str | None
    vol_20d: float | None

    def to_dict(self) -> dict:
        return asdict(self)


def build_context(
    symbol: str,
    frame: pd.DataFrame,
    benchmark: pd.DataFrame | None = None,
) -> Context:
    """Summarise one symbol's latest state.

    `benchmark` is SPY's frame. When it is absent the excess-return fields come
    back as None rather than silently reporting absolute return as if it were
    relative — a number that flatters every name in a bull market.
    """
    close = frame["close"]
    listing = BY_SYMBOL.get(symbol)

    window = close.iloc[-BARS_PER_YEAR:] if len(close) >= BARS_PER_YEAR else close
    high_52w = float(window.max())
    low_52w = float(window.min())

    sma20 = sma(close, 20).iloc[-1]
    sma50 = sma(close, 50).iloc[-1]
    trend = (
        None
        if math.isnan(sma20) or math.isnan(sma50)
        else ("up" if sma20 > sma50 else "down")
    )

    excess_1m = excess_1y = None
    if benchmark is not None and symbol != "SPY":
        bench_close = benchmark["close"]
        own_1m, bench_1m = _pct_change(close, BARS_PER_MONTH), _pct_change(bench_close, BARS_PER_MONTH)
        own_1y, bench_1y = _pct_change(close, BARS_PER_YEAR), _pct_change(bench_close, BARS_PER_YEAR)
        if own_1m is not None and bench_1m is not None:
            excess_1m = own_1m - bench_1m
        if own_1y is not None and bench_1y is not None:
            excess_1y = own_1y - bench_1y

    return Context(
        symbol=symbol,
        name=listing.name if listing else symbol,
        sector=listing.sector if listing else "",
        last_bar=frame.index[-1].strftime("%Y-%m-%d"),
        close=round(float(close.iloc[-1]), 2),
        chg_1d=_pct_change(close, 1),
        chg_1m=_pct_change(close, BARS_PER_MONTH),
        chg_3m=_pct_change(close, BARS_PER_QUARTER),
        chg_1y=_pct_change(close, BARS_PER_YEAR),
        excess_1m=excess_1m,
        excess_1y=excess_1y,
        high_52w=round(high_52w, 2),
        low_52w=round(low_52w, 2),
        from_high=float(close.iloc[-1] / high_52w - 1.0),
        rsi=_finite(rsi(close).iloc[-1]),
        trend=trend,
        vol_20d=_finite(realized_volatility(close).iloc[-1]),
    )


def build_all(frames: Mapping[str, pd.DataFrame]) -> dict[str, Context]:
    """Context for every symbol, with SPY used as the benchmark leg."""
    benchmark = frames.get("SPY")
    return {
        symbol: build_context(symbol, frame, benchmark)
        for symbol, frame in frames.items()
    }
