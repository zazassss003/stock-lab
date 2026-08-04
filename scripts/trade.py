"""Run one decision cycle. Dry run unless you explicitly say otherwise.

    py -3 scripts/trade.py                    # simulated broker, dry run
    py -3 scripts/trade.py --alpaca           # Alpaca paper account, dry run
    py -3 scripts/trade.py --alpaca --live    # actually submits paper orders

`--live` means live *paper* orders. There is no flag in this repository that
sends a real-money order, and adding one is not a small change: the Alpaca
adapter refuses any non-paper endpoint in its constructor.

Nothing here has passed walk-forward validation. Run scripts/research.py and
read the verdicts before you enable submission of anything.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stocklab.data.yfinance_source import YFinanceSource  # noqa: E402
from stocklab.execution import RiskLimits, SimulatedBroker, Trader  # noqa: E402
from stocklab.pipeline import CACHE_DIR  # noqa: E402
from stocklab.strategy.donchian import DonchianBreakout  # noqa: E402

HALT_FILE = ROOT / "HALT"
JOURNAL = ROOT / "journal.jsonl"


def main() -> int:
    symbol = next((a for a in sys.argv[1:] if not a.startswith("-")), "SPY")
    use_alpaca = "--alpaca" in sys.argv
    enable_trading = "--live" in sys.argv

    bars = YFinanceSource(cache_dir=CACHE_DIR).fetch([symbol])

    if use_alpaca:
        from stocklab.execution.alpaca import AlpacaPaperBroker

        broker = AlpacaPaperBroker()
    else:
        broker = SimulatedBroker(cash=100_000.0)
        broker.set_prices({symbol: float(bars[symbol]["close"].iloc[-1])})

    trader = Trader(
        broker=broker,
        strategy=DonchianBreakout(symbol, entry=55, exit=20),
        limits=RiskLimits(max_position_notional=1_000.0, max_orders_per_day=10),
        enable_trading=enable_trading,
        journal_path=JOURNAL,
        halt_file=HALT_FILE,
    )

    decision = trader.step(bars)

    print(f"\n{symbol}  bars through {bars[symbol].index[-1].date()}")
    print(f"  equity     ${decision.equity:,.2f}   cash ${decision.cash:,.2f}")
    print(f"  positions  {decision.positions or '{}'}")
    print(f"  targets    {decision.targets or '{}'}")
    print(f"  orders     {decision.intended_orders or 'none'}")
    print(f"  result     {decision.note}")
    print(f"  journalled {JOURNAL.name}\n")

    if enable_trading:
        print("  Trading was ENABLED. Create a file named HALT in the repo root")
        print("  to stop the loop on its next cycle, from anywhere, instantly.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
