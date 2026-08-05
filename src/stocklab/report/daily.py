"""The daily brief: what the system decided today, and why.

Deterministic by design. What the system "did" is the output of a rule, so the
report states the rule's inputs and its threshold rather than describing them.
An LLM summary of a decision can drift from the decision; this cannot.

Kept short on purpose. A report you skim every day has to fit on one screen, or
you stop reading it — and the day it matters is the day you skipped.

**The rendered report is deliberately pure ASCII.** This machine's console and
editors default to cp950, where a stray `·` becomes `繚` and a piped read
raises UnicodeDecodeError. Prose in this module can be typographic; the output
cannot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

import pandas as pd


@dataclass(frozen=True)
class SignalLine:
    symbol: str
    target: float  # fraction of equity the rule wants held
    close: float
    reason: str

    @property
    def state(self) -> str:
        return "long" if self.target > 0 else "flat"


def donchian_levels(frame: pd.DataFrame, entry: int, exit: int) -> tuple[float, float]:
    """Entry and exit thresholds for the *next* bar.

    Both windows exclude the current bar, matching the strategy exactly — a
    report that quoted different levels than the rule uses would be worse than
    no report.
    """
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    return float(highs[-entry:].max()), float(lows[-exit:].min())


def explain_donchian(frame: pd.DataFrame, target: float, entry: int, exit: int) -> str:
    """One line saying what would have to happen for the position to change."""
    entry_high, exit_low = donchian_levels(frame, entry, exit)
    close = float(frame["close"].iloc[-1])

    if target > 0:
        gap = close / exit_low - 1.0
        return f"exits below {exit_low:,.2f} ({exit}d low), {gap:.1%} away"

    gap = entry_high / close - 1.0
    return f"enters above {entry_high:,.2f} ({entry}d high), {gap:.1%} away"


def load_previous(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            return {}
    return {}


def save_state(path: Path, signals: list[SignalLine]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({s.symbol: s.target for s in signals}, indent=0),
        encoding="utf-8",
    )


def build_report(
    signals: list[SignalLine],
    previous: Mapping[str, float],
    *,
    data_age_days: int,
    last_bar: str,
    symbols_updated: int,
    stale_symbols: list[str],
    unpriced: Mapping[str, list],
    trading_enabled: bool,
    halted: bool,
    strategy_label: str,
    validated: bool,
    today: date | None = None,
) -> str:
    """Render the brief. Order is deliberate: what changed, then why, then health."""
    today = today or date.today()
    lines: list[str] = []

    lines.append(f"stock-lab | {today:%Y-%m-%d}")
    lines.append("")

    # 1. What changed. This is the only part that is ever news.
    changed = [s for s in signals if previous.get(s.symbol) != s.target and s.symbol in previous]
    new_symbols = [s for s in signals if s.symbol not in previous]

    lines.append("WHAT CHANGED")
    if not previous:
        lines.append("  First run - no prior state to compare against.")
    elif not changed:
        lines.append("  Nothing. No position would have changed today.")
    else:
        for s in changed:
            was = "long" if previous[s.symbol] > 0 else "flat"
            lines.append(f"  {s.symbol:<7} {was} -> {s.state.upper()}   {s.reason}")
    if new_symbols:
        lines.append(f"  Now tracking: {', '.join(s.symbol for s in new_symbols)}")
    lines.append("")

    # 2. The full position and the threshold that would move it.
    lines.append(f"POSITIONS | {strategy_label}")
    for s in signals:
        lines.append(f"  {s.symbol:<7} {s.state:<5} {s.close:>10,.2f}   {s.reason}")
    lines.append("")

    # 3. Health. Boring on a good day, which is the point.
    lines.append("DATA")
    health = "OK" if data_age_days <= 5 and not stale_symbols else "CHECK"
    lines.append(f"  {symbols_updated} symbols | newest bar {last_bar} ({data_age_days}d old) | {health}")
    if stale_symbols:
        lines.append(f"  Stale: {', '.join(stale_symbols)}")
    if unpriced:
        detail = "; ".join(f"{k} {len(v)}" for k, v in unpriced.items())
        lines.append(f"  Dropped unpriced sessions: {detail}")
    lines.append("")

    # 4. Standing state. Repeated daily on purpose — it is the thing that
    #    would otherwise quietly drift out of mind.
    lines.append("STATUS")
    if halted:
        lines.append("  HALTED - kill switch is active. No orders will be sent.")
    lines.append(
        f"  Trading {'ENABLED' if trading_enabled else 'disabled (dry run)'} - "
        f"{'orders are being submitted' if trading_enabled else 'nothing is submitted'}."
    )
    if not validated:
        lines.append("  No strategy has passed validation. Positions above are")
        lines.append("  monitoring output, not a recommendation to act.")

    return "\n".join(lines) + "\n"
