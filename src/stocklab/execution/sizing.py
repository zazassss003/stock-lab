"""Turning target weights into orders — shared by the backtest and the trader.

This module exists so those two can never disagree. If the backtest sizes
positions one way and the live loop another, every backtest number becomes a
statement about a system that isn't the one running, and the discrepancy shows
up as unexplained live underperformance months later.

One function, both callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


# Fees and slippage are paid *out of* cash, so sizing to exactly 100% of
# equity ends the cycle slightly overdrawn — harmless in a backtest, an
# insufficient-buying-power rejection at a real broker. Hold a little back.
#
# INVARIANT: cash_buffer must exceed round-trip cost (slippage + fee), or a
# full allocation still overdraws. 0.2% covers the engine defaults (5bps
# slippage + 1bp fee) with room to spare; raise it if you raise those.
DEFAULT_CASH_BUFFER = 0.002


@dataclass(frozen=True)
class SizedOrder:
    symbol: str
    delta_shares: float  # positive = buy, negative = sell
    reference_price: float

    @property
    def notional(self) -> float:
        return self.delta_shares * self.reference_price


def compute_orders(
    targets: Mapping[str, float],
    positions: Mapping[str, float],
    prices: Mapping[str, float],
    equity: float,
    rebalance_band: float = 0.0,
    min_trade_notional: float = 1.0,
    cash_buffer: float = DEFAULT_CASH_BUFFER,
) -> list[SizedOrder]:
    """Orders that move `positions` toward `targets`, sells first.

    `rebalance_band` is a fraction of equity: drift smaller than this is left
    alone. Without it, a constant-weight target generates a trade every bar
    forever, paying fees to chase rounding.

    Sells lead so a rebalance never needs cash it does not yet have — buying
    first can drive the balance negative mid-cycle, which a real broker
    rejects and a naive backtest silently allows.
    """
    if equity <= 0:
        return []

    investable = equity * (1.0 - cash_buffer)

    orders: list[SizedOrder] = []
    for symbol, price in prices.items():
        if price <= 0:
            continue

        held = positions.get(symbol, 0.0)
        current_weight = held * price / equity
        target_weight = targets.get(symbol, 0.0)

        if abs(target_weight - current_weight) < rebalance_band:
            continue

        delta = target_weight * investable / price - held
        if abs(delta * price) < min_trade_notional:
            continue

        orders.append(SizedOrder(symbol=symbol, delta_shares=delta, reference_price=price))

    return sorted(orders, key=lambda order: order.delta_shares)
