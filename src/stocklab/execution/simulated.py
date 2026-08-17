"""An offline broker that fills instantly at a price you set.

Exists so the entire live path — strategy, sizing, risk limits, kill switch,
journal — can be tested end to end with no network, no keys and no account.
Everything the Alpaca adapter does, this does deterministically.
"""

from __future__ import annotations

from ..backtest.costs import FLAT_DEFAULT, CostModel
from .broker import Order


class SimulatedBroker:
    def __init__(
        self,
        cash: float = 100_000.0,
        costs: CostModel = FLAT_DEFAULT,
        qty_increment: float = 0.0,
        ready: bool = True,
    ) -> None:
        # The same `CostModel` the backtest uses, on purpose: if this charged
        # its own version of "a fee", the dry-run numbers would drift from the
        # backtest numbers and nobody would know which one was wrong.
        self.costs = costs
        self.qty_increment = float(qty_increment)
        self._cash = float(cash)
        self._positions: dict[str, float] = {}
        self._prices: dict[str, float] = {}
        self._ready = ready
        self.submitted: list[Order] = []

    def set_prices(self, prices: dict[str, float]) -> None:
        self._prices.update(prices)

    def set_ready(self, ready: bool) -> None:
        """Simulate a gateway going away, so the trader's response can be tested."""
        self._ready = ready

    def is_ready(self) -> bool:
        return self._ready

    def submit(self, order: Order) -> str:
        price = self._prices.get(order.symbol)
        if price is None:
            raise ValueError(f"no price set for {order.symbol}")

        signed = order.qty if order.side == "buy" else -order.qty
        fill_price = self.costs.fill_price(price, signed)
        notional = signed * fill_price
        commission, regulatory = self.costs.trade_cost(signed, fill_price)

        self._cash -= notional + commission + regulatory
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
