"""Builds the self-contained HTML dashboard.

The page has no server and no external requests: the whole dataset is embedded
as JSON and the charts are hand-drawn SVG. That keeps it openable from disk,
e-mailable, and safe to publish — but it also means the *data* is a snapshot.
Re-run the script to refresh it.

Multi-symbol: one file holds every tracked name, switched client-side. Derived
series (moving averages, RSI) are computed here in Python rather than in the
page's JavaScript, so there is exactly one implementation of each indicator and
the charts cannot quietly disagree with the backtest.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping

import pandas as pd

from ..backtest.engine import BacktestResult
from ..universe import BY_SYMBOL

TEMPLATE = Path(__file__).with_name("template.html")

BARS_PER_MONTH = 21
BARS_PER_YEAR = 252


def _clean(values, digits: int) -> list[float | None]:
    """Round for payload size, turning NaN into null so JSON stays valid."""
    out: list[float | None] = []
    for value in values:
        number = float(value)
        out.append(None if math.isnan(number) else round(number, digits))
    return out


def _drawdown(equity: pd.Series) -> pd.Series:
    """Percentage below the running peak — always <= 0."""
    return equity / equity.cummax() - 1.0


def _change(close: pd.Series, bars: int) -> float | None:
    if len(close) <= bars:
        return None
    return round(float(close.iloc[-1] / close.iloc[-1 - bars] - 1.0), 6)


def build_symbol_block(
    symbol: str,
    frame: pd.DataFrame,
    results: Mapping[str, tuple[str, BacktestResult]],
) -> dict:
    """Everything the page needs about one symbol."""
    from ..features.indicators import rsi, sma

    close = frame["close"]
    listing = BY_SYMBOL.get(symbol)

    strategies = {}
    for key, (label, result) in results.items():
        strategies[key] = {
            "label": label,
            # Whole dollars and two-decimal percentages: the extra precision was
            # invisible on screen and cost ~250KB across the universe.
            "equity": _clean(result.equity, 0),
            "drawdown": _clean(_drawdown(result.equity) * 100.0, 2),
            "stats": {k: round(v, 6) for k, v in result.stats.items()},
            "trades": [
                {
                    "date": fill.ts.strftime("%Y-%m-%d"),
                    "symbol": fill.symbol,
                    "side": "buy" if fill.qty > 0 else "sell",
                    "qty": round(fill.qty, 3),
                    "price": round(fill.price, 2),
                    "fee": round(fill.fee, 2),
                }
                for fill in result.fills
            ],
        }

    sma20 = sma(close, 20)
    sma50 = sma(close, 50)
    rsi14 = rsi(close)

    return {
        "symbol": symbol,
        "name": listing.name if listing else symbol,
        "sector": listing.sector if listing else "",
        "start": frame.index[0].strftime("%Y-%m-%d"),
        "end": frame.index[-1].strftime("%Y-%m-%d"),
        "dates": [ts.strftime("%Y-%m-%d") for ts in frame.index],
        "price": {
            "close": _clean(close, 2),
            "sma20": _clean(sma20, 2),
            "sma50": _clean(sma50, 2),
            "rsi": _clean(rsi14, 1),
        },
        "summary": {
            "last": round(float(close.iloc[-1]), 2),
            "chg_1d": _change(close, 1),
            "chg_1m": _change(close, BARS_PER_MONTH),
            "chg_1y": _change(close, BARS_PER_YEAR),
            "rsi": None if math.isnan(rsi14.iloc[-1]) else round(float(rsi14.iloc[-1]), 1),
            # Trend state, not a recommendation: which side of the slow average
            # the fast one sits on. Null until both averages exist.
            "trend": (
                None
                if math.isnan(sma20.iloc[-1]) or math.isnan(sma50.iloc[-1])
                else ("up" if sma20.iloc[-1] > sma50.iloc[-1] else "down")
            ),
        },
        "strategies": strategies,
    }


def build_payload(blocks: Mapping[str, dict], order: list[str]) -> dict:
    """Assemble every symbol block into one JSON-serialisable document."""
    blocks = {symbol: dict(block) for symbol, block in blocks.items()}
    payload = {
        "generated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "order": [s for s in order if s in blocks],
        "symbols": blocks,
    }

    # US large caps share one trading calendar, so the date array is usually
    # identical across the universe — storing it once instead of eleven times
    # is worth ~350KB. The page restores it per symbol on load.
    date_arrays = [block["dates"] for block in blocks.values()]
    if len(date_arrays) > 1 and all(dates == date_arrays[0] for dates in date_arrays):
        payload["dates"] = date_arrays[0]
        for block in blocks.values():
            del block["dates"]

    return payload


def render_dashboard(payload: dict, output: str | Path) -> Path:
    """Inject the payload into the template and write the standalone page."""
    template = TEMPLATE.read_text(encoding="utf-8")

    # `</` inside a <script> block would close the tag early; escaping it keeps
    # the document well-formed whatever ends up in the data.
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    path = Path(output)
    path.write_text(template.replace("__PAYLOAD__", data), encoding="utf-8")
    return path
