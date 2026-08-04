from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_bars(
    n_bars: int = 60,
    symbols: tuple[str, ...] = ("AAA",),
    seed: int = 0,
) -> dict[str, pd.DataFrame]:
    """Synthetic OHLCV that satisfies the data contract. No network in tests."""
    index = pd.date_range("2024-01-01", periods=n_bars, freq="B", tz="UTC")
    rng = np.random.default_rng(seed)

    bars: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, n_bars)))
        open_ = close * (1.0 + rng.normal(0.0, 0.001, n_bars))
        bars[symbol] = pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum(open_, close) * 1.005,
                "low": np.minimum(open_, close) * 0.995,
                "close": close,
                "volume": rng.integers(100_000, 1_000_000, n_bars).astype(float),
            },
            index=index,
        )
    return bars


@pytest.fixture
def bars() -> dict[str, pd.DataFrame]:
    return make_bars()
