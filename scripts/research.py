"""Walk-forward every strategy and print an honest verdict for each.

    py -3 scripts/research.py [SYMBOL]

This is the script that tells you *not* to trade something. Expect most rows
to say REJECT — that is the harness working, not failing.
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
from stocklab.strategy.momentum import TimeSeriesMomentum  # noqa: E402
from stocklab.strategy.rsi_reversion import RsiMeanReversion  # noqa: E402
from stocklab.strategy.sma_cross import SmaCross  # noqa: E402
from stocklab.strategy.vol_target import VolatilityTargetedMomentum  # noqa: E402

# Trades smaller than the band are noise that only pays fees. Imported rather
# than redeclared so research and the dashboard cannot describe different
# systems while appearing to agree.
RUN_KWARGS = {"rebalance_band": REBALANCE_BAND}


def grids(symbol: str) -> dict:
    """Every candidate, with the full parameter grid that will be searched.

    The grid sizes are the `trials` count in the output. They are deliberately
    visible: a bigger grid is a weaker claim, and hiding it is how backtests
    become sales pitches.
    """
    return {
        "SMA crossover": (
            lambda p: SmaCross(symbol, p["fast"], p["slow"]),
            [
                {"fast": f, "slow": s}
                for f in (10, 20, 50)
                for s in (50, 100, 200)
                if f < s
            ],
        ),
        "Time-series momentum": (
            lambda p: TimeSeriesMomentum(symbol, p["lookback"]),
            [{"lookback": n} for n in (63, 126, 252)],
        ),
        "Vol-targeted momentum": (
            lambda p: VolatilityTargetedMomentum(symbol, p["lookback"], 20, p["target_vol"]),
            [
                {"lookback": n, "target_vol": v}
                for n in (126, 252)
                for v in (0.10, 0.15, 0.20)
            ],
        ),
        "RSI mean reversion": (
            lambda p: RsiMeanReversion(symbol, 14, p["entry"], p["exit"]),
            [
                {"entry": e, "exit": x}
                for e in (25, 30, 35)
                for x in (50, 55, 60)
            ],
        ),
        "Donchian breakout": (
            lambda p: DonchianBreakout(symbol, p["entry"], p["exit"]),
            [
                {"entry": e, "exit": x}
                for e in (20, 55)
                for x in (10, 20)
            ],
        ),
    }


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    bars = YFinanceSource(cache_dir=CACHE_DIR).fetch([symbol])
    index = bars[symbol].index
    print(f"\n{symbol}: {len(index)} bars, {index[0].date()} to {index[-1].date()}")
    print("Walk-forward: 3y train -> 6m test, rolling. Only test windows are scored.\n")

    header = (
        f"{'strategy':<24}{'cfgs':>5}{'evals':>7}{'OOS ret':>9}"
        f"{'sharpe':>8}{'maxDD':>8}{'keep':>7}{'DSR':>6}{'DSRc':>6}"
    )
    print(header)
    print("-" * len(header))

    verdicts = []
    oos_window = None
    for name, (factory, grid) in grids(symbol).items():
        result = walk_forward(bars, factory, grid, **RUN_KWARGS)
        s = result.summary
        if not s:
            print(f"{name:<24}  not enough history")
            continue

        oos_window = (result.folds[0].test_start, result.folds[-1].test_end)
        print(
            f"{name:<24}{int(s['configs']):>5}{int(s['trials']):>7}"
            f"{s['oos_total_return']:>9.1%}{s['oos_sharpe']:>8.2f}"
            f"{s['oos_max_drawdown']:>8.1%}{s['retention']:>7.0%}"
            f"{s['deflated_sharpe']:>6.2f}{s['deflated_sharpe_by_config']:>6.2f}"
        )
        verdicts.append((name, result.verdict))

    # The row that decides everything. A strategy that beats zero but loses to
    # doing nothing is not an edge — it is an expensive way to own less market.
    if oos_window:
        benchmark = slice_result(run(bars, BuyAndHold(), **RUN_KWARGS), *oos_window).stats
        print("-" * len(header))
        print(
            f"{'Buy & hold (benchmark)':<24}{'-':>5}{1:>7}"
            f"{benchmark['total_return']:>9.1%}{benchmark['sharpe']:>8.2f}"
            f"{benchmark['max_drawdown']:>8.1%}{'-':>7}{'-':>6}{'-':>6}"
        )

    print("\nVerdicts")
    for name, verdict in verdicts:
        print(f"  {name:<24} {verdict}")

    print(
        "\n  cfgs  = distinct parameter sets searched.   evals = cfgs x folds."
        "\n  keep  = out-of-sample Sharpe / in-sample Sharpe (healthy is 50-70%)."
        "\n  DSR   = deflated Sharpe counting every evaluation (conservative)."
        "\n  DSRc  = deflated Sharpe counting distinct configurations (lenient)."
        "\n          Below 0.95 is not evidence, however good the return looks."
        "\n          Where the two straddle 0.95, the verdict rests on a debatable"
        "\n          choice of N and is reported BORDERLINE rather than settled."
        "\n  Cash earns 0% here, which penalises strategies that sit flat —"
        "\n          conservative, not flattering.\n"
    )


if __name__ == "__main__":
    main()
