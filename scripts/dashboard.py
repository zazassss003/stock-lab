"""Generate the standalone HTML dashboard.

    py -3 scripts/dashboard.py [SYMBOL]

Writes dashboard.html next to the repo root. Open it directly in a browser —
no server, no network, everything embedded.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stocklab.backtest import engine  # noqa: E402
from stocklab.data.yfinance_source import YFinanceSource  # noqa: E402
from stocklab.report.dashboard import build_payload, render_dashboard  # noqa: E402
from stocklab.strategy.buy_and_hold import BuyAndHold  # noqa: E402
from stocklab.strategy.sma_cross import SmaCross  # noqa: E402

START, END = "2019-01-01", "2024-01-01"


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"

    bars = YFinanceSource().fetch([symbol], START, END)
    results = {
        "buy_hold": ("Buy & hold", engine.run(bars, BuyAndHold())),
        "sma_cross": ("SMA 20/50", engine.run(bars, SmaCross(symbol, 20, 50))),
    }

    payload = build_payload(symbol, bars, results)
    path = render_dashboard(payload, ROOT / "dashboard.html")

    size_kb = path.stat().st_size / 1024
    print(f"wrote {path} ({size_kb:,.0f} KB, {len(payload['dates'])} bars)")
    for key, (label, result) in results.items():
        stats = result.stats
        print(f"  {label:<12} return {stats['total_return']:>7.1%}   sharpe {stats['sharpe']:>5.2f}")


if __name__ == "__main__":
    main()
