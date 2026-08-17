"""What each broker's fee schedule would have cost, on real bars.

    py -3 scripts/costs.py [SYMBOL] [--cash 20000]

Fee comparison tables answer "what is the commission". That is the wrong
question, and it is the only one they answer. This runs the same strategies
through the same bars under each published schedule and reports the cost that
actually landed — including the two layers the tables leave out: statutory fees
and, for an account funded from Taiwan, the currency conversion.

The interesting result is usually that commission is not the deciding term.
A commission-free broker reached by bank wire can lose to a broker charging a
$1 minimum, because a retail FX spread on the funding transfer is worth more
than years of tickets. Which one wins depends on how much you move and how
often you trade — so the script takes both as arguments rather than declaring
a winner.

This compares *cost schedules*. It is not a recommendation of a broker, and
none of these strategies has passed validation; see scripts/research.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stocklab.backtest.costs import PRESETS  # noqa: E402
from stocklab.backtest.engine import run  # noqa: E402
from stocklab.data.yfinance_source import YFinanceSource  # noqa: E402
from stocklab.pipeline import CACHE_DIR, REBALANCE_BAND  # noqa: E402
from stocklab.strategy.buy_and_hold import BuyAndHold  # noqa: E402
from stocklab.strategy.donchian import DonchianBreakout  # noqa: E402
from stocklab.strategy.sma_cross import SmaCross  # noqa: E402

# One rarely-trading rule and one often-trading rule. Cost structures rank
# differently under each, which is the entire point: a per-ticket minimum is
# free to a buy-and-hold account and expensive to one that rebalances weekly.
def profiles(symbol: str) -> dict:
    return {
        "buy & hold": BuyAndHold(),
        "SMA 20/50": SmaCross(symbol, 20, 50),
        "Donchian 55/20": DonchianBreakout(symbol, 55, 20),
    }


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    symbol = (args[0] if args else "SPY").upper()

    cash = 20_000.0
    if "--cash" in sys.argv:
        cash = float(sys.argv[sys.argv.index("--cash") + 1])

    bars = YFinanceSource(cache_dir=CACHE_DIR).fetch([symbol])
    index = bars[symbol].index
    print(
        f"\n{symbol}  {index[0].date()} → {index[-1].date()}  "
        f"({len(index)} bars, ${cash:,.0f} committed)\n"
    )

    # A fresh strategy per run: some hold state across bars, and reusing one
    # would make the second schedule score a half-finished object.
    for label in profiles(symbol):
        print(f"{label}")
        print(
            f"  {'schedule':<22}{'commission':>11}{'reg':>8}{'slippage':>10}"
            f"{'FX':>9}{'total':>10}{'drag':>8}{'net ret':>10}"
        )

        for key, costs in PRESETS.items():
            result = run(
                bars,
                profiles(symbol)[label],
                initial_cash=cash,
                costs=costs,
                rebalance_band=REBALANCE_BAND,
            )
            stats = result.stats
            total = (
                stats["total_commission"]
                + stats["total_regulatory"]
                + stats["total_slippage"]
                + stats["fx_cost"]
            )
            print(
                f"  {key:<22}{stats['total_commission']:>11,.0f}"
                f"{stats['total_regulatory']:>8,.0f}{stats['total_slippage']:>10,.0f}"
                f"{stats['fx_cost']:>9,.0f}{total:>10,.0f}"
                f"{total / cash:>7.1%}{stats['net_return']:>10.1%}"
            )
        print()

    print(
        "drag = total cost as a fraction of the money committed.\n"
        "net ret = return after paying to get the money in and back out again;\n"
        "  for schedules that do not price currency it equals the gross return,\n"
        "  which is an understatement rather than a measurement.\n"
        "Rates are the published schedules as of 2026-08 — verify before relying\n"
        "on any of them. See src/stocklab/backtest/costs.py.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
