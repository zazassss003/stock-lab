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

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stocklab.data.yfinance_source import YFinanceSource  # noqa: E402
from stocklab.execution import RiskLimits, SimulatedBroker, Trader  # noqa: E402
from stocklab.execution.trader import Decision  # noqa: E402
from stocklab.pipeline import CACHE_DIR  # noqa: E402
from stocklab.strategy.donchian import DonchianBreakout  # noqa: E402

HALT_FILE = ROOT / "HALT"
JOURNAL = ROOT / "journal.jsonl"


def _journal_data_failure(error: Exception) -> None:
    """Record a refresh failure as a decision rather than dying with a trace.

    `trader.py` refuses to let "the broker was unreachable" look like "the
    strategy wanted no change", because in a journal those are the same line and
    opposite facts. A data failure has exactly that shape, one step earlier: if
    the scheduled run exits with a stack trace, the journal simply has no entry
    for that day, and weeks later a gap is indistinguishable from a quiet
    market. This has already happened twice — 2026-08-08 and 08-09 both failed
    this way and left nothing behind but a traceback in update.log.

    The provider failing is common enough (rate limits, a bad tail request) that
    it needs a normal representation, not an exception.
    """
    decision = Decision(
        ts=str(pd.Timestamp.now(tz="UTC")),
        equity=0.0,
        cash=0.0,
        positions={},
        targets={},
        note=f"BLOCKED - data refresh failed: {error}",
    )
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(decision)) + "\n")


def main() -> int:
    symbol = next((a for a in sys.argv[1:] if not a.startswith("-")), "SPY")
    use_alpaca = "--alpaca" in sys.argv
    enable_trading = "--live" in sys.argv

    try:
        bars = YFinanceSource(cache_dir=CACHE_DIR).fetch([symbol])
    except Exception as error:
        # Non-zero exit so the scheduler records a failure and the operator can
        # see it, but the journal gets its line either way.
        _journal_data_failure(error)
        print(f"\n{symbol}  BLOCKED - data refresh failed: {error}")
        print(f"  journalled {JOURNAL.name}\n")
        return 1

    if use_alpaca:
        from stocklab.execution.alpaca import AlpacaPaperBroker

        broker = AlpacaPaperBroker()
    else:
        broker = SimulatedBroker(cash=100_000.0)
        broker.set_prices({symbol: float(bars[symbol]["close"].iloc[-1])})

    # Sized from the account the broker actually reports rather than from
    # constants, so the caps cannot silently drift out of range of the orders
    # the strategy asks for. See RiskLimits.for_equity for why that matters.
    price_now = float(bars[symbol]["close"].iloc[-1])
    equity_now = broker.cash() + broker.positions().get(symbol, 0.0) * price_now
    limits = RiskLimits.for_equity(equity_now)

    trader = Trader(
        broker=broker,
        strategy=DonchianBreakout(symbol, entry=55, exit=20),
        limits=limits,
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
    # Printed because a breach is only diagnosable next to the limit it broke.
    # ASCII only: this prints to a console that may be cp950 or cp1252, where a
    # middle dot arrives as mojibake and makes correct output look broken.
    print(
        f"  caps       max order ${limits.max_position_notional:,.0f}"
        f"  |  max daily loss ${limits.max_daily_loss:,.0f}"
        f"  |  {limits.max_orders_per_day} orders/day"
    )
    print(f"  result     {decision.note}")
    print(f"  journalled {JOURNAL.name}\n")

    if enable_trading:
        print("  Trading was ENABLED. Create a file named HALT in the repo root")
        print("  to stop the loop on its next cycle, from anywhere, instantly.\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
