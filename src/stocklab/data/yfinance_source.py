"""yfinance adapter — free daily bars, incrementally cached and kept current.

Not a production feed: unofficial, rate-limited, and it occasionally revises
history. Fine for research; replace with the Alpaca adapter before stage 4.

Two behaviours here exist to keep a *tracking* tool honest:

- The cache is keyed by symbol alone and holds the full history, so a moving
  end date appends a few bars instead of re-downloading years.
- A bar for a session that has not closed yet is dropped. An in-progress
  "close" is not a close, and feeding one to a strategy produces a signal that
  silently changes after the fact.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import pandas as pd

from .source import OHLCV_COLUMNS, validate_bars

DEFAULT_START = "2015-01-01"

# US equities close at 16:00 ET. Before that, today's bar is still forming.
MARKET_TZ = "America/New_York"
MARKET_CLOSE_HOUR = 16


def today_utc() -> date:
    return pd.Timestamp.now(tz="UTC").date()


def drop_incomplete_session(df: pd.DataFrame, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Remove a final bar belonging to a session that has not closed yet.

    The index date *is* the session date, so it is compared against the current
    date in market time rather than converted (a UTC-midnight daily stamp
    converts backwards into the previous ET day).
    """
    if df.empty:
        return df

    now = now if now is not None else pd.Timestamp.now(tz=MARKET_TZ)
    now = now.tz_convert(MARKET_TZ) if now.tzinfo else now.tz_localize(MARKET_TZ)

    if df.index[-1].date() == now.date() and now.hour < MARKET_CLOSE_HOUR:
        return df.iloc[:-1]
    return df


def drop_unpriced(df: pd.DataFrame) -> tuple[pd.DataFrame, list[pd.Timestamp]]:
    """Split off rows with no usable prices, returning them for reporting.

    Yahoo publishes the newest bar's volume before its adjustment factor, so
    with `auto_adjust=True` the most recent row can arrive with real volume and
    NaN OHLC. A bar without prices is not a bar — but dropping it silently is
    how a broken feed passes for a quiet market, so the caller is told.
    """
    price_columns = ["open", "high", "low", "close"]
    bad = df[price_columns].isna().any(axis=1)
    return df.loc[~bad], list(df.index[bad])


def merge_bars(cached: pd.DataFrame | None, fresh: pd.DataFrame) -> pd.DataFrame:
    """Combine cached and freshly downloaded bars, newer winning on overlap.

    Overlap is deliberate: the provider revises recent bars (late splits,
    dividend adjustments), so the tail is re-fetched and allowed to overwrite.
    """
    if cached is None or cached.empty:
        combined = fresh
    else:
        combined = pd.concat([cached, fresh])
        combined = combined[~combined.index.duplicated(keep="last")]
    return combined.sort_index()


class YFinanceSource:
    """Fetches daily bars, caching full history per symbol on disk."""

    def __init__(self, cache_dir: str | Path | None = "data_store", refresh_days: int = 7) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.refresh_days = refresh_days
        # Populated per fetch() so callers can surface provider defects.
        self.dropped_unpriced: dict[str, list[pd.Timestamp]] = {}
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(
        self,
        symbols: Sequence[str],
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Return bars from `start` through `end`, defaulting to today.

        Leaving `end` unset is the intended usage for a tracker — pin it only
        when you deliberately want a frozen window for a reproducible study.
        """
        start = start or DEFAULT_START
        end = end or str(today_utc())
        self.dropped_unpriced = {}

        bars = {symbol: self._history(symbol, start, end) for symbol in symbols}
        bars = _align(bars)
        validate_bars(bars)
        return bars

    def _history(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        cached = self._load(symbol)

        if cached is None or cached.empty:
            fresh = self._download(symbol, start, end)
        else:
            # Re-fetch a short tail so revisions land, and back-fill if the
            # caller now wants history from earlier than the cache holds.
            tail_from = (cached.index[-1] - pd.Timedelta(days=self.refresh_days)).date()
            fresh = self._download(symbol, str(tail_from), end)
            if pd.Timestamp(start, tz="UTC") < cached.index[0]:
                fresh = merge_bars(self._download(symbol, start, str(cached.index[0].date())), fresh)

        # Filter after merging, not on download, so a bad row already sitting
        # in the cache is cleaned too. Unusable rows are never persisted, so a
        # recent one is re-fetched (and re-reported) until the provider fixes
        # it, while an old one falls outside the refresh window and stays gone.
        merged, dropped = drop_unpriced(merge_bars(cached, fresh))
        if dropped:
            self.dropped_unpriced.setdefault(symbol, []).extend(dropped)
        self._save(merged, symbol)

        window = merged.loc[
            (merged.index >= pd.Timestamp(start, tz="UTC")) & (merged.index <= pd.Timestamp(end, tz="UTC"))
        ]
        return drop_incomplete_session(window)

    def _download(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        import yfinance as yf  # imported lazily so tests need no network

        # yfinance treats `end` as exclusive; step past it to include today.
        end_exclusive = str(pd.Timestamp(end).date() + timedelta(days=1))

        raw = yf.download(
            symbol,
            start=start,
            end=end_exclusive,
            interval="1d",
            auto_adjust=True,  # split- and dividend-adjusted
            progress=False,
        )
        if raw.empty:
            raise ValueError(f"{symbol}: no data returned for {start}..{end}")

        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw.rename(columns=str.lower)[list(OHLCV_COLUMNS)]
        df.index = pd.DatetimeIndex(df.index).tz_localize(None).tz_localize("UTC")
        return df.sort_index()

    def _path(self, symbol: str) -> Path | None:
        return self.cache_dir / f"{symbol}.parquet" if self.cache_dir else None

    def _load(self, symbol: str) -> pd.DataFrame | None:
        path = self._path(symbol)
        if path and path.exists():
            return pd.read_parquet(path)
        return None

    def _save(self, df: pd.DataFrame, symbol: str) -> None:
        path = self._path(symbol)
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
