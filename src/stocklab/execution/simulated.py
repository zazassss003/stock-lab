"""An offline broker that fills instantly at a price you set.

Exists so the entire live path — strategy, sizing, risk limits, kill switch,
journal — can be tested end to end with no network, no keys and no account.
Everything the Alpaca adapter does, this does deterministically.
"""

from __future__ import annotations

from .broker import Order


class SimulatedBroker:
    def __init__(
        self,
        cash: float = 100_000.0,
        fee_bps: float = 1.0,
        slippage_bps: float = 5.0,
    ) -> None:
        self._cash = float(cash)
        self._positions: dict[str, float] = {}
        self._prices: dict[str, float] = {}
        self._fee_rate = fee_bps / 10_000.0
        self._slip_rate = slippage_bps / 10_000.0
        self.submitted: list[Order] = []

    def set_prices(self, prices: dict[str, float]) -> None:
        self._prices.update(prices)

    def submit(self, order: Order) -> str:
        price = self._prices.get(order.symbol)
        if price is None:
            raise ValueError(f"no price set for {order.symbol}")

        signed = order.qty if order.side == "buy" else -order.qty
        direction = 1.0 if signed > 0 else -1.0
        fill_price = price * (1.0 + direction * self._slip_rate)
        notional = signed * fill_price
        fee = abs(notional) * self._fee_rate

        self._cash -= notional + fee
        self._positions[order.symbol] = self._positions.get(order.symbol, 0.0) + signed
        self.submitted.append(order)
        return f"sim-{len(self.submitted)}"

    def positions(self) -> dict[str, float]:
        return dict(self._positions)

    def cash(self) -> float:
        return self._cash

    def equity(self) -> float:
        held = sum(qty * self._prices.get(symbol, 0.0) for symbol, qty in self._positions.items())
        return self._cash + held
