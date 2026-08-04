"""Demo: trace real prices, run two strategies, compare them honestly.

    py -3 scripts/demo.py

Needs network on first run, then reads the parquet cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from stocklab.backtest import engine  # noqa: E402
from stocklab.data.yfinance_source import YFinanceSource  # noqa: E402
from stocklab.features.indicators import rsi, sma  # noqa: E402
from stocklab.strategy.buy_and_hold import BuyAndHold  # noqa: E402
from stocklab.strategy.sma_cross import SmaCross  # noqa: E402

SYMBOL = "SPY"


def main() -> None:
    bars = YFinanceSource().fetch([SYMBOL])
    close = bars[SYMBOL]["close"]

    start = close.index[0].date()
    end = close.index[-1].date()
    print(f"\n[1] Traced {SYMBOL}: {len(close)} bars, {start} to {end}")
    print(f"    last close {close.iloc[-1]:.2f}")

    print("\n[2] Indicators (latest values)")
    print(f"    SMA(20) {sma(close, 20).iloc[-1]:.2f}")
    print(f"    SMA(50) {sma(close, 50).iloc[-1]:.2f}")
    print(f"    RSI(14) {rsi(close).iloc[-1]:.1f}")

    print("\n[3] Backtest, costs included (1bp fee, 5bp slippage)")
    contenders = {
        "buy & hold": BuyAndHold(),
        "SMA 20/50": SmaCross(SYMBOL, fast=20, slow=50),
    }

    header = f"    {'':<12}{'return':>10}{'CAGR':>9}{'maxDD':>9}{'sharpe':>9}{'fills':>7}"
    print(header)
    print("    " + "-" * (len(header) - 4))

    for name, strategy in contenders.items():
        stats = engine.run(bars, strategy).stats
        print(
            f"    {name:<12}"
            f"{stats['total_return']:>9.1%}"
            f"{stats['cagr']:>9.1%}"
            f"{stats['max_drawdown']:>9.1%}"
            f"{stats['sharpe']:>9.2f}"
            f"{int(stats['n_fills']):>7}"
        )

    print("\n    One symbol, one period, one parameter pair. This is an")
    print("    illustration of the pipeline, not evidence about either idea.\n")


if __name__ == "__main__":
    main()
