"""Re-test the borderline strategies on symbols that had no say in their design.

    py -3 scripts/research_universe.py

Why this exists: the deflated Sharpe corrects for searching within a parameter
grid. It cannot correct for the *grid itself* having been chosen by someone who
already knew how the period played out. The only defence is data that played no
part in the choice — here, the ten tracked companies, when every parameter grid
was picked looking at SPY.

**The survivorship problem, and how this handles it.** Those ten names were
selected because they are the most profitable companies *today*. A long-only
trend strategy will look wonderful on ten known winners regardless of merit. So
nothing here is judged on absolute return. Every strategy is scored **against
buy & hold on the same symbol, over the same window** — both carry the identical
survivorship bias, so comparing them cancels it out.

The question asked is therefore not "did it make money?" (it will) but
"**did it beat simply owning the thing?**"
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stocklab.backtest.engine import run  # noqa: E402
from stocklab.backtest.walkforward import slice_result, walk_forward  # noqa: E402
from stocklab.data.yfinance_source import YFinanceSource  # noqa: E402
from stocklab.pipeline import CACHE_DIR, REBALANCE_BAND  # noqa: E402
from stocklab.strategy.buy_and_hold import BuyAndHold  # noqa: E402
from stocklab.strategy.donchian import DonchianBreakout  # noqa: E402
from stocklab.strategy.sma_cross import SmaCross  # noqa: E402
from stocklab.universe import TOP_PROFIT  # noqa: E402

RUN_KWARGS = {"rebalance_band": REBALANCE_BAND}

# Only the strategies that came back BORDERLINE on SPY. Re-testing the ones
# already rejected would just be more searching, which is the opposite of what
# this script is for.
CANDIDATES = {
    "SMA crossover": (
        lambda s: (lambda p: SmaCross(s, p["fast"], p["slow"])),
        [{"fast": f, "slow": sl} for f in (10, 20, 50) for sl in (50, 100, 200) if f < sl],
    ),
    "Donchian breakout": (
        lambda s: (lambda p: DonchianBreakout(s, p["entry"], p["exit"])),
        [{"entry": e, "exit": x} for e in (20, 55) for x in (10, 20)],
    ),
}


def main() -> None:
    source = YFinanceSource(cache_dir=CACHE_DIR)
    symbols = [listing.symbol for listing in TOP_PROFIT]

    print(f"\nRe-testing on {len(symbols)} symbols that played no part in choosing these grids.")
    print("Scored against buy & hold on the SAME symbol, so survivorship bias cancels.\n")

    for name, (factory_for, grid) in CANDIDATES.items():
        header = (
            f"{'symbol':<8}{'strategy':>10}{'buy&hold':>10}{'diff':>9}"
            f"{'shrp':>7}{'bh shrp':>9}{'maxDD':>8}{'bh maxDD':>10}{'DSRc':>7}"
        )
        print(f"=== {name} ===")
        print(header)
        print("-" * len(header))

        beat_return = 0
        beat_sharpe = 0
        beat_drawdown = 0
        evaluated = 0

        for symbol in symbols:
            try:
                bars = source.fetch([symbol])
            except Exception as error:
                print(f"{symbol:<8}  fetch failed: {error}")
                continue

            result = walk_forward(bars, factory_for(symbol), grid, **RUN_KWARGS)
            s = result.summary
            if not s:
                print(f"{symbol:<8}  not enough history")
                continue

            window = (result.folds[0].test_start, result.folds[-1].test_end)
            bench = slice_result(run(bars, BuyAndHold(), **RUN_KWARGS), *window).stats

            evaluated += 1
            diff = s["oos_total_return"] - bench["total_return"]
            beat_return += diff > 0
            beat_sharpe += s["oos_sharpe"] > bench["sharpe"]
            # Less negative is better; a shallower hole is a real improvement.
            beat_drawdown += s["oos_max_drawdown"] > bench["max_drawdown"]

            print(
                f"{symbol:<8}{s['oos_total_return']:>10.1%}{bench['total_return']:>10.1%}"
                f"{diff:>9.1%}{s['oos_sharpe']:>7.2f}{bench['sharpe']:>9.2f}"
                f"{s['oos_max_drawdown']:>8.1%}{bench['max_drawdown']:>10.1%}"
                f"{s['deflated_sharpe_by_config']:>7.3f}"
            )

        if evaluated:
            print("-" * len(header))
            print(
                f"beat buy & hold on return:   {beat_return}/{evaluated}\n"
                f"beat buy & hold on Sharpe:   {beat_sharpe}/{evaluated}\n"
                f"shallower drawdown:          {beat_drawdown}/{evaluated}\n"
            )

    print(
        "  Reading this: beating buy & hold on Sharpe and drawdown while losing on\n"
        "  return means the strategy is not adding edge, it is holding less market.\n"
        "  That is a real property, but it is not the same as making more money —\n"
        "  and it is available for free by simply buying less.\n"
    )


if __name__ == "__main__":
    main()
