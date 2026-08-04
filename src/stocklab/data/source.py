"""Data source interface and the shape contract every adapter must satisfy.

Swapping yfinance for Alpaca (or a Taiwan source) means writing another class
that satisfies `DataSource` and returns frames that pass `validate_bars`.
Nothing downstream should need to change.
"""

from __future__ import annotations

from typing import Protocol, Sequence

import pandas as pd

OHLCV_COLUMNS = ("open", "high", "low", "close", "volume")


class DataSource(Protocol):
    """Fetches historical bars. Adapters are the only place network I/O lives."""

    def fetch(
        self,
        symbols: Sequence[str],
        start: str,
        end: str,
    ) -> dict[str, pd.DataFrame]:
        """Return {symbol: DataFrame} with a tz-aware UTC DatetimeIndex,
        `OHLCV_COLUMNS` as lowercase columns, sorted ascending, split- and
        dividend-adjusted. All frames share one index.
        """
        ...


def validate_bars(bars: dict[str, pd.DataFrame]) -> None:
    """Raise if any frame violates the contract.

    Called at the boundary so bad data fails loudly here rather than showing up
    as an impossibly good backtest three stages later.
    """
    if not bars:
        raise ValueError("no symbols supplied")

    reference_index: pd.DatetimeIndex | None = None

    for symbol, df in bars.items():
        missing = set(OHLCV_COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"{symbol}: missing columns {sorted(missing)}")

        if not isinstance(df.index, pd.DatetimeIndex):
            raise TypeError(f"{symbol}: index must be a DatetimeIndex")
        if df.index.tz is None:
            raise ValueError(f"{symbol}: index must be timezone-aware (UTC)")
        if not df.index.is_monotonic_increasing:
            raise ValueError(f"{symbol}: index must be sorted ascending")
        if df.index.has_duplicates:
            dupes = df.index[df.index.duplicated()].tolist()
            raise ValueError(f"{symbol}: duplicate timestamps {dupes[:5]}")

        if df[list(OHLCV_COLUMNS)].isna().any().any():
            raise ValueError(f"{symbol}: NaNs present in OHLCV")
        if (df["high"] < df["low"]).any():
            raise ValueError(f"{symbol}: high < low on some bars")
        if (df[["open", "high", "low", "close"]] <= 0).any().any():
            raise ValueError(f"{symbol}: non-positive prices")

        if reference_index is None:
            reference_index = df.index
        elif not df.index.equals(reference_index):
            raise ValueError(f"{symbol}: index does not match other symbols")
