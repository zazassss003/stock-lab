"""End-to-end smoke test: fetch real bars, run the benchmark, print stats.

    py -3 scripts/smoke.py

Needs network on the first run; afterwards it reads the parquet cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stocklab.backtest import engine  # noqa: E402
from stocklab.data.yfinance_source import YFinanceSource  # noqa: E402
from stocklab.strategy.buy_and_hold import BuyAndHold  # noqa: E402


def main() -> None:
    bars = YFinanceSource().fetch(["SPY", "QQQ"], "2023-01-01", "2024-01-01")
    print("bars:", {symbol: len(df) for symbol, df in bars.items()})

    result = engine.run(bars, BuyAndHold())
    for name, value in result.stats.items():
        print(f"  {name:>14}: {value:,.4f}")


if __name__ == "__main__":
    main()
