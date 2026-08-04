"""yfinance adapter — free daily bars, good enough for stages 1 through 3.

Not a production feed: it is unofficial, rate-limited and occasionally revises
history. Fine for research; replace with the Alpaca adapter before stage 4.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pandas as pd

from .source import OHLCV_COLUMNS, validate_bars


class YFinanceSource:
    """Fetches daily bars, with an optional on-disk parquet cache.

    Caching is not an optimisation here — it makes runs reproducible. Without
    it, re-running the same backtest next week can silently use revised data.
    """

    def __init__(self, cache_dir: str | Path | None = "data_store") -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(
        self,
        symbols: Sequence[str],
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]:
        bars: dict[str, pd.DataFrame] = {}
        for symbol in symbols:
            df = self._load_cached(symbol, start, end)
            if df is None:
                df = self._download(symbol, start, end)
                self._save_cached(df, symbol, start, end)
            bars[symbol] = df

        bars = _align(bars)
        validate_bars(bars)
        return bars

    def _download(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        import yfinance as yf  # imported lazily so tests need no network

        raw = yf.download(
            symbol,
            start=start,
            end=end,
            interval="1d",
            auto_adjust=True,  # split- and dividend-adjusted
            progress=False,
        )
        if raw.empty:
            raise ValueError(f"{symbol}: no data returned for {start}..{end}")

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw.rename(columns=str.lower)[list(OHLCV_COLUMNS)]
        df.index = pd.DatetimeIndex(df.index).tz_localize("UTC")
        return df.sort_index()

    def _cache_path(self, symbol: str, start: str, end: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"{symbol}_{start}_{end}.parquet"

    def _load_cached(self, symbol: str, start: str, end: str) -> pd.DataFrame | None:
        path = self._cache_path(symbol, start, end)
        if path and path.exists():
            return pd.read_parquet(path)
        return None

    def _save_cached(self, df: pd.DataFrame, symbol: str, start: str, end: str) -> None:
        path = self._cache_path(symbol, start, end)
        if path:
            df.to_parquet(path)


def _align(bars: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Restrict every symbol to timestamps present in all of them.

    Dropping a partially-present bar is the safe choice: carrying one symbol
    forward while another moves invents prices that never traded.
    """
    if len(bars) == 1:
        return bars

    common = None
    for df in bars.values():
        common = df.index if common is None else common.intersection(df.index)

    return {symbol: df.loc[common] for symbol, df in bars.items()}
