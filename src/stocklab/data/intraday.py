"""Intraday bars — for *watching* the market, not for backtesting it.

Daily bars cannot render a 60-minute or 24-hour chart, so this is a separate
data path with its own cache and its own rules.

**It is deliberately not wired into the backtest engine.** Every strategy,
every walk-forward result and every cost assumption in this repo is calibrated
to daily bars. Feeding intraday bars to the same engine would produce numbers
that look like backtests and are not — the fee and slippage model alone is
wrong by an order of magnitude at that frequency. Intraday is presentation
only, and the dashboard says so on the chart.

Provider limits (measured, not assumed):

| interval | history available | bars per session |
|----------|-------------------|------------------|
| 1m       | 7 days            | 390              |
| 5m       | 60 days           | 78               |
| 60m      | 730 days          | ~7               |
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd

from .source import OHLCV_COLUMNS

# period is the largest the provider will serve for that interval.
INTERVALS: dict[str, str] = {"1m": "7d", "5m": "60d", "60m": "730d"}

# How much to keep in the payload. Slicing is by *bar count*, not wall clock:
# at 04:00 on a Sunday, "the last 60 minutes" of wall clock contains no
# trading at all, and an empty chart is not what anyone means by it.
KEEP_BARS: dict[str, int] = {"1m": 180, "5m": 240, "60m": 200}


class IntradaySource:
    """Fetches intraday bars, cached per symbol and interval.

    Unlike the daily source this does not merge history forward: intraday
    windows roll out of availability at the provider, so the cache is a
    snapshot used as an offline fallback rather than an accumulating archive.
    """

    def __init__(self, cache_dir: str | Path | None = "data_store/intraday") -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.failures: dict[str, str] = {}
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, symbols: Sequence[str], interval: str = "5m") -> dict[str, pd.DataFrame]:
        if interval not in INTERVALS:
            raise ValueError(f"unsupported interval {interval!r}; expected one of {sorted(INTERVALS)}")

        self.failures = {}
        bars: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            try:
                frame = self._download(symbol, interval)
                self._save(frame, symbol, interval)
            except Exception as error:
                # An intraday gap must never cost you the daily dashboard.
                cached = self._load(symbol, interval)
                if cached is None:
                    self.failures[symbol] = str(error)
                    continue
                frame = cached
                self.failures[symbol] = f"{error} (served from cache)"

            bars[symbol] = frame.tail(KEEP_BARS[interval])
        return bars

    def _download(self, symbol: str, interval: str) -> pd.DataFrame:
        import yfinance as yf

        raw = yf.download(
            symbol,
            period=INTERVALS[interval],
            interval=interval,
            auto_adjust=True,
            progress=False,
        )
        if raw.empty:
            raise ValueError(f"{symbol}: no {interval} data returned")

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        frame = raw.rename(columns=str.lower)[list(OHLCV_COLUMNS)]
        # Provider returns market time; normalise to UTC like everything else.
        index = pd.DatetimeIndex(frame.index)
        frame.index = index.tz_convert("UTC") if index.tz else index.tz_localize("UTC")

        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        return frame.dropna(subset=["open", "high", "low", "close"])

    def _path(self, symbol: str, interval: str) -> Path | None:
        return self.cache_dir / f"{symbol}_{interval}.parquet" if self.cache_dir else None

    def _load(self, symbol: str, interval: str) -> pd.DataFrame | None:
        path = self._path(symbol, interval)
        if path and path.exists():
            return pd.read_parquet(path)
        return None

    def _save(self, frame: pd.DataFrame, symbol: str, interval: str) -> None:
        path = self._path(symbol, interval)
        if path:
            frame.to_parquet(path)


def last_quote(symbol: str) -> dict:
    """Most recent price the provider will give, with its own timestamp.

    Not a real-time feed: Yahoo's quote is delayed, and how delayed varies by
    exchange. The timestamp is returned alongside the price precisely so the
    page can show how old it is rather than implying it is current.
    """
    import yfinance as yf

    info = yf.Ticker(symbol).fast_info
    price = info.get("lastPrice")
    return {
        "price": None if price is None else round(float(price), 2),
        "previous_close": (
            None if info.get("previousClose") is None else round(float(info["previousClose"]), 2)
        ),
        "as_of": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC"),
    }
