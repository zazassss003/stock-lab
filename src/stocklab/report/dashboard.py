"""Builds the self-contained HTML dashboard.

The page has no server and no external requests: the whole dataset is embedded
as JSON and the charts are hand-drawn SVG. That keeps it openable from disk,
e-mailable, and safe to publish — but it also means the *data* is a snapshot.
Re-run this script to refresh it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping

import pandas as pd

from ..backtest.engine import BacktestResult

TEMPLATE = Path(__file__).with_name("template.html")


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


def build_payload(
    symbol: str,
    bars: Mapping[str, pd.DataFrame],
    results: Mapping[str, tuple[str, BacktestResult]],
) -> dict:
    """Assemble everything the page needs into one JSON-serialisable dict."""
    from ..features.indicators import rsi, sma

    frame = bars[symbol]
    close = frame["close"]

    strategies = {}
    for key, (label, result) in results.items():
        strategies[key] = {
            "label": label,
            "equity": _clean(result.equity, 2),
            "drawdown": _clean(_drawdown(result.equity) * 100.0, 3),
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

    return {
        "symbol": symbol,
        "start": frame.index[0].strftime("%Y-%m-%d"),
        "end": frame.index[-1].strftime("%Y-%m-%d"),
        "generated": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
        "dates": [ts.strftime("%Y-%m-%d") for ts in frame.index],
        "price": {
            "close": _clean(close, 2),
            "sma20": _clean(sma(close, 20), 2),
            "sma50": _clean(sma(close, 50), 2),
            "rsi": _clean(rsi(close), 1),
        },
        "strategies": strategies,
    }


def render_dashboard(payload: dict, output: str | Path) -> Path:
    """Inject the payload into the template and write the standalone page."""
    template = TEMPLATE.read_text(encoding="utf-8")

    # `</` inside a <script> block would close the tag early; escaping it keeps
    # the document well-formed whatever ends up in the data.
    data = json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")

    path = Path(output)
    path.write_text(template.replace("__PAYLOAD__", data), encoding="utf-8")
    return path
