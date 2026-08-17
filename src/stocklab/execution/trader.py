"""The live loop: data in, decision out, orders only if explicitly allowed.

Deliberately boring and deterministic — no LLM, no randomness. Given the same
bars and the same account state it produces the same orders, every time, so a
decision months old can be replayed and explained.

Safety is layered, and every layer defaults to *not trading*:

1. `enable_trading` is **False** by default. The loop computes and journals
   everything and submits nothing. Run it this way for weeks first; the
   journal tells you what it would have done.
2. A **kill switch file** halts the loop regardless of configuration. Creating
   a file is something you can do in a panic from any device, without editing
   code or restarting anything.
3. `RiskLimits` are checked per order. When trading is live a breach blocks the
   whole cycle rather than clamping the order, because a strategy asking for
   something absurd is a bug, and shrinking it to something merely bad hides
   that. In dry run the breach is *recorded* instead — no order can leave the
   process either way, and a dry run whose only output is one BLOCKED line per
   day teaches nothing about what the rule wanted.
4. Every cycle is journalled to JSONL for reconciliation — the record of what
   the system intended, to compare against what the broker actually did.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Mapping

import pandas as pd

from ..strategy.base import Strategy
from .broker import Order, RiskLimits
from .sizing import compute_orders


@dataclass
class Decision:
    ts: str
    equity: float
    cash: float
    positions: dict[str, float]
    targets: dict[str, float]
    intended_orders: list[dict] = field(default_factory=list)
    submitted_ids: list[str] = field(default_factory=list)
    traded: bool = False
    note: str = ""


class Trader:
    def __init__(
        self,
        broker,
        strategy: Strategy,
        limits: RiskLimits | None = None,
        enable_trading: bool = False,
        rebalance_band: float = 0.02,
        journal_path: str | Path | None = None,
        halt_file: str | Path | None = None,
    ) -> None:
        self.broker = broker
        self.strategy = strategy
        self.limits = limits or RiskLimits()
        self.enable_trading = enable_trading
        self.rebalance_band = rebalance_band
        self.journal_path = Path(journal_path) if journal_path else None
        self.halt_file = Path(halt_file) if halt_file else None

        self._day: date | None = None
        self._orders_today = 0
        self._day_start_equity: float | None = None

    def halted_reason(self) -> str | None:
        if self.limits.halted:
            return "kill switch set in RiskLimits"
        if self.halt_file and self.halt_file.exists():
            return f"kill switch file present: {self.halt_file}"
        return None

    def step(self, bars: Mapping[str, pd.DataFrame], now: pd.Timestamp | None = None) -> Decision:
        """One decision cycle over the bars available *now*."""
        now = now or pd.Timestamp.now(tz="UTC")
        prices = {symbol: float(df["close"].iloc[-1]) for symbol, df in bars.items()}

        positions = self.broker.positions()
        cash = self.broker.cash()
        equity = cash + sum(positions.get(s, 0.0) * p for s, p in prices.items())

        self._roll_day(now, equity)

        decision = Decision(
            ts=str(now),
            equity=equity,
            cash=cash,
            positions=dict(positions),
            targets={},
        )

        halted = self.halted_reason()
        if halted:
            decision.note = f"HALTED — {halted}"
            return self._journal(decision)

        # Asked before deciding anything. A broker behind a local gateway can be
        # absent without raising, and "the gateway was down" must never be
        # journalled as "the strategy wanted no change" — those look identical
        # in the record and mean opposite things.
        if not self._broker_ready():
            decision.note = "BLOCKED — broker not ready (connection or account state)"
            return self._journal(decision)

        targets = dict(self.strategy.on_bar(bars))
        decision.targets = targets

        orders = compute_orders(
            targets=targets,
            positions=positions,
            prices=prices,
            equity=equity,
            rebalance_band=self.rebalance_band,
            qty_increment=getattr(self.broker, "qty_increment", 0.0),
        )
        decision.intended_orders = [
            {"symbol": o.symbol, "qty": round(o.delta_shares, 6), "notional": round(o.notional, 2)}
            for o in orders
        ]

        if not orders:
            decision.note = "no action — inside rebalance band"
            return self._journal(decision)

        pnl_today = equity - (self._day_start_equity or equity)
        breach = self._first_breach(orders, pnl_today)

        # The order of these two branches is the whole point. A breach must stop
        # a *live* cycle outright — but in dry run nothing leaves the process
        # anyway, so aborting here would destroy the record the dry run exists
        # to build. Checking `enable_trading` first is what lets weeks of
        # journal say "this is what the rule wanted, and this is the limit that
        # would have stopped it" instead of one opaque BLOCKED line per day.
        if not self.enable_trading:
            decision.note = (
                f"DRY RUN — nothing submitted; would have been blocked: {breach}"
                if breach
                else "DRY RUN — trading disabled, nothing submitted"
            )
            return self._journal(decision)

        if breach:
            # Block the entire cycle, not just the offending order — a partial
            # rebalance leaves the portfolio somewhere nobody chose.
            decision.note = f"BLOCKED — {breach}"
            return self._journal(decision)

        for order in orders:
            broker_order = Order(
                symbol=order.symbol,
                side="buy" if order.delta_shares > 0 else "sell",
                qty=abs(order.delta_shares),
            )
            decision.submitted_ids.append(self.broker.submit(broker_order))
            self._orders_today += 1

        decision.traded = True
        decision.note = f"submitted {len(decision.submitted_ids)} order(s)"
        return self._journal(decision)

    def _first_breach(self, orders, pnl_today: float) -> str | None:
        """The first risk limit this cycle would violate, or None.

        Reported rather than raised so the caller can decide what a breach
        means: fatal when trading is live, informative when it is not.
        """
        for order in orders:
            broker_order = Order(
                symbol=order.symbol,
                side="buy" if order.delta_shares > 0 else "sell",
                qty=abs(order.delta_shares),
            )
            try:
                self.limits.check(broker_order, order.reference_price, self._orders_today, pnl_today)
            except RuntimeError as error:
                return str(error)
        return None

    def _broker_ready(self) -> bool:
        """A broker without `is_ready` is assumed reachable — it just answered."""
        probe = getattr(self.broker, "is_ready", None)
        if probe is None:
            return True
        try:
            return bool(probe())
        except Exception:
            return False

    def _roll_day(self, now: pd.Timestamp, equity: float) -> None:
        if self._day != now.date():
            self._day = now.date()
            self._orders_today = 0
            self._day_start_equity = equity

    def _journal(self, decision: Decision) -> Decision:
        if self.journal_path:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(decision)) + "\n")
        return decision
