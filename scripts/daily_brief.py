"""Print (and save) the daily brief: what the system decided today, and why.

    py -3 scripts/daily_brief.py

Run automatically by scripts/update.py after the data refresh, so the 18:00
scheduled task produces it without any extra wiring.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stocklab.data.yfinance_source import YFinanceSource  # noqa: E402
from stocklab.execution.trader import Trader  # noqa: E402
from stocklab.pipeline import CACHE_DIR, STALE_AFTER_DAYS  # noqa: E402
from stocklab.report.daily import (  # noqa: E402
    SignalLine,
    build_report,
    explain_donchian,
    load_previous,
    save_state,
)
from stocklab.strategy.donchian import DonchianBreakout  # noqa: E402
from stocklab.universe import SYMBOLS  # noqa: E402

import pandas as pd  # noqa: E402

REPORT = ROOT / "daily_report.md"
STATE = ROOT / "research_store" / "daily_state.json"
HALT_FILE = ROOT / "HALT"

# The configured rule. Donchian is REJECTED by walk-forward — it is here so the
# report describes something concrete, and the STATUS block says so every day.
ENTRY, EXIT = 55, 20
STRATEGY_LABEL = f"Donchian {ENTRY}/{EXIT} (not validated)"


def main() -> int:
    source = YFinanceSource(cache_dir=CACHE_DIR)
    signals: list[SignalLine] = []
    ages: list[int] = []
    stale: list[str] = []
    last_bars: list[pd.Timestamp] = []
    today = pd.Timestamp.now(tz="UTC").normalize()

    for symbol in SYMBOLS:
        try:
            bars = source.fetch([symbol])
        except Exception:
            stale.append(symbol)
            continue

        frame = bars[symbol]
        strategy = DonchianBreakout(symbol, ENTRY, EXIT)

        # Replay the rule over history so its position state is correct today,
        # exactly as the backtest would have carried it.
        target = 0.0
        for i in range(len(frame)):
            target = strategy.on_bar({symbol: frame.iloc[: i + 1]}).get(symbol, 0.0)

        signals.append(
            SignalLine(
                symbol=symbol,
                target=target,
                close=float(frame["close"].iloc[-1]),
                reason=explain_donchian(frame, target, ENTRY, EXIT),
            )
        )

        age = (today - frame.index[-1].normalize()).days
        ages.append(age)
        last_bars.append(frame.index[-1])
        if age > STALE_AFTER_DAYS:
            stale.append(symbol)

    if not signals:
        print("no symbols could be read", file=sys.stderr)
        return 1

    report = build_report(
        signals,
        load_previous(STATE),
        data_age_days=max(ages),
        last_bar=max(last_bars).strftime("%Y-%m-%d"),
        symbols_updated=len(signals),
        stale_symbols=stale,
        unpriced=source.dropped_unpriced,
        trading_enabled=False,  # matches scripts/trade.py's default
        halted=HALT_FILE.exists(),
        strategy_label=STRATEGY_LABEL,
        validated=False,
        today=today.date(),
    )

    print(report)
    REPORT.write_text(report, encoding="utf-8")
    save_state(STATE, signals)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
