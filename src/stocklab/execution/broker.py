"""Broker interface and risk limits — stage 4 groundwork.

Deliberately unimplemented. The adapter written against this must target
Alpaca's **paper** endpoint. Do not add a live-money endpoint here; that is a
decision to be made by hand, later, after months of paper results.

`RiskLimits` exists in this file rather than in an adapter because it must
apply to every broker, including the live one that may never be written.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class Order:
    symbol: str
    side: Side
    qty: float


@dataclass(frozen=True)
class RiskLimits:
    """Hard bounds checked before any order leaves the process.

    These are a circuit breaker, not a strategy parameter. A strategy asking to
    exceed them is a bug in the strategy; the correct response is to refuse the
    order and halt, never to clamp silently and carry on.
    """

    max_position_notional: float = 1_000.0
    max_orders_per_day: int = 10
    max_daily_loss: float = 100.0
    halted: bool = False  # the kill switch

    #: Headroom above a full allocation. A strategy may legitimately want 100%
    #: of equity in one name, so the cap must clear that, and the slack covers
    #: the gap between the price a decision used and the price a fill gets.
    FULL_ALLOCATION_MARGIN = 1.10

    #: A day this bad is worth stopping to look at, not a normal red day.
    DAILY_LOSS_FRACTION = 0.02

    @classmethod
    def for_equity(cls, equity: float, max_orders_per_day: int = 10) -> "RiskLimits":
        """Caps scaled to the account, because absolute defaults rot.

        The defaults on this class are deliberately tiny — appropriate for a
        first cautious experiment and wrong for everything after. Left alone
        against a $100,000 account, the $1,000 notional cap breached on every
        order a fully-invested strategy asked for, and a fortnight of dry-run
        journal recorded nothing but the breach. A cap that can never be
        satisfied is not a safety feature, it is an off switch nobody chose.

        Deriving them from equity keeps them meaning the same thing at any
        account size: refuse the order that asks for a multiple of what exists,
        and stop the day that has already gone badly.
        """
        if equity <= 0:
            raise ValueError(f"equity must be positive to size risk limits, got {equity}")

        return cls(
            max_position_notional=equity * cls.FULL_ALLOCATION_MARGIN,
            max_orders_per_day=max_orders_per_day,
            max_daily_loss=equity * cls.DAILY_LOSS_FRACTION,
        )

    def check(self, order: Order, price: float, orders_today: int, pnl_today: float) -> None:
        if self.halted:
            raise RuntimeError("trading halted by kill switch")
        if abs(order.qty * price) > self.max_position_notional:
            raise RuntimeError(f"order notional exceeds {self.max_position_notional}")
        if orders_today >= self.max_orders_per_day:
            raise RuntimeError(f"daily order cap {self.max_orders_per_day} reached")
        if pnl_today < -abs(self.max_daily_loss):
            raise RuntimeError(f"daily loss limit {self.max_daily_loss} breached")


class Broker(Protocol):
    """What the trader needs from any venue, paper or otherwise.

    Two members exist because of brokers this repository does not yet talk to,
    and both default to the permissive answer so the Alpaca adapter is
    unaffected:

    - `qty_increment` — Alpaca fills fractional shares, so sizing has always
      assumed it could hit a target exactly. Most other venues cannot. A broker
      that only trades whole shares reports 1.0 and sizing rounds toward zero.
    - `is_ready()` — a REST broker is reachable or it raises. A broker reached
      through a local gateway process (IBKR's TWS/IB Gateway) can be *absent*
      without anything raising, and a trader that cannot tell the difference
      journals "no action today" on a day it was simply disconnected. That is
      the worst possible failure: a silent one that looks like a decision.
    """

    #: Smallest tradeable lot. 0.0 means fractional shares are allowed.
    qty_increment: float

    def submit(self, order: Order) -> str:
        """Send an order, return a broker order id."""
        ...

    def positions(self) -> dict[str, float]:
        """Current holdings in shares, keyed by symbol."""
        ...

    def cash(self) -> float:
        """Settled cash available to trade, in USD."""
        ...

    def is_ready(self) -> bool:
        """Whether this broker can be trusted to answer right now."""
        ...
