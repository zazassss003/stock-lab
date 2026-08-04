"""Tests for the live path.

Every safety layer is tested to *not* trade, because the expensive failure in
this module is an order that should never have left the process.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from stocklab.execution import RiskLimits, SimulatedBroker, Trader, compute_orders
from stocklab.execution.broker import Order

from conftest import make_bars


class AlwaysLong:
    def on_bar(self, history):
        return {"AAA": 1.0}


class AlwaysFlat:
    def on_bar(self, history):
        return {"AAA": 0.0}


class Flipping:
    """Alternates in and out, so every cycle generates a real order."""

    def __init__(self) -> None:
        self._long = False

    def on_bar(self, history):
        self._long = not self._long
        return {"AAA": 1.0 if self._long else 0.0}


# --------------------------------------------------------------- sizing


def test_sizing_sells_before_buys():
    orders = compute_orders(
        targets={"AAA": 0.5, "BBB": 0.0},
        positions={"AAA": 0.0, "BBB": 100.0},
        prices={"AAA": 10.0, "BBB": 10.0},
        equity=2000.0,
    )
    assert [o.symbol for o in orders] == ["BBB", "AAA"]
    assert orders[0].delta_shares < 0 < orders[1].delta_shares


def test_rebalance_band_suppresses_small_drift():
    common = dict(
        targets={"AAA": 0.50},
        positions={"AAA": 51.0},  # 51% of equity — 1% adrift
        prices={"AAA": 10.0},
        equity=1000.0,
    )
    assert compute_orders(**common, rebalance_band=0.0)
    assert compute_orders(**common, rebalance_band=0.02) == []


def _allocate_fully(fee_bps: float, slippage_bps: float, cash_buffer: float) -> float:
    """Cash left after putting 100% of equity to work. Negative = overdrawn."""
    price = 250.0
    broker = SimulatedBroker(cash=100_000.0, fee_bps=fee_bps, slippage_bps=slippage_bps)
    broker.set_prices({"AAA": price})

    orders = compute_orders(
        {"AAA": 1.0}, {}, {"AAA": price}, equity=100_000.0, cash_buffer=cash_buffer
    )
    for order in orders:
        broker.submit(Order("AAA", "buy", abs(order.delta_shares)))
    return broker.cash()


def test_full_allocation_leaves_cash_for_costs_at_default_settings():
    """A 100% target must not overdraw once fees are paid.

    Alpaca rejects an order exceeding buying power, so a backtest quietly
    running cash negative would not be reproducible live.
    """
    from stocklab.execution.sizing import DEFAULT_CASH_BUFFER

    remaining = _allocate_fully(fee_bps=1.0, slippage_bps=5.0, cash_buffer=DEFAULT_CASH_BUFFER)
    assert remaining >= 0.0, f"overdrawn by {-remaining:.2f}"


def test_the_buffer_must_exceed_round_trip_cost():
    """The documented invariant, asserted rather than trusted.

    A buffer smaller than slippage + fees still overdraws — this pins the
    relationship so nobody raises costs later without raising the buffer.
    """
    assert _allocate_fully(fee_bps=10.0, slippage_bps=50.0, cash_buffer=0.002) < 0.0
    assert _allocate_fully(fee_bps=10.0, slippage_bps=50.0, cash_buffer=0.01) >= 0.0


def test_sizing_refuses_to_act_on_worthless_account():
    assert compute_orders({"AAA": 1.0}, {}, {"AAA": 10.0}, equity=0.0) == []


def test_backtest_and_live_share_one_sizing_path():
    """Parity is the point of the shared module — assert it explicitly."""
    from stocklab.backtest import engine

    assert engine.compute_orders is compute_orders


# --------------------------------------------------------------- broker


def test_simulated_broker_applies_costs_against_you():
    broker = SimulatedBroker(cash=1000.0, fee_bps=10.0, slippage_bps=100.0)
    broker.set_prices({"AAA": 10.0})

    broker.submit(Order("AAA", "buy", 10.0))

    assert broker.positions()["AAA"] == 10.0
    # 10 shares at 10.10 (slipped up) plus fee — strictly worse than 100.
    assert broker.cash() < 900.0


def test_simulated_broker_rejects_unpriced_symbol():
    with pytest.raises(ValueError, match="no price"):
        SimulatedBroker().submit(Order("ZZZ", "buy", 1.0))


# --------------------------------------------------------------- trader


def _trader(**kwargs):
    bars = make_bars(n_bars=60, symbols=("AAA",))
    broker = SimulatedBroker(cash=100_000.0)
    broker.set_prices({"AAA": float(bars["AAA"]["close"].iloc[-1])})
    return bars, broker, Trader(broker=broker, strategy=AlwaysLong(), **kwargs)


def test_dry_run_is_the_default_and_submits_nothing():
    bars, broker, trader = _trader(limits=RiskLimits(max_position_notional=1e9))

    decision = trader.step(bars)

    assert decision.intended_orders, "should still compute what it would do"
    assert decision.traded is False
    assert broker.submitted == []
    assert "DRY RUN" in decision.note


def test_enabling_trading_actually_submits():
    bars, broker, trader = _trader(
        enable_trading=True, limits=RiskLimits(max_position_notional=1e9)
    )

    decision = trader.step(bars)

    assert decision.traded is True
    assert broker.submitted and decision.submitted_ids


def test_kill_switch_file_halts_before_anything_is_computed(tmp_path):
    halt = tmp_path / "HALT"
    halt.write_text("stop")
    bars, broker, trader = _trader(enable_trading=True, halt_file=halt)

    decision = trader.step(bars)

    assert decision.traded is False
    assert broker.submitted == []
    assert "HALTED" in decision.note
    assert decision.targets == {}, "must not even consult the strategy"


def test_risk_limit_blocks_the_whole_cycle_not_just_one_order():
    bars, broker, trader = _trader(
        enable_trading=True, limits=RiskLimits(max_position_notional=100.0)
    )

    decision = trader.step(bars)

    assert decision.traded is False
    assert broker.submitted == []
    assert "BLOCKED" in decision.note


def test_halted_risk_limits_also_stop_trading():
    bars, broker, trader = _trader(enable_trading=True, limits=RiskLimits(halted=True))

    assert broker.submitted == []
    assert "HALTED" in trader.step(bars).note


def test_no_orders_when_already_on_target():
    bars = make_bars(n_bars=60, symbols=("AAA",))
    broker = SimulatedBroker(cash=100_000.0)
    broker.set_prices({"AAA": float(bars["AAA"]["close"].iloc[-1])})
    trader = Trader(broker=broker, strategy=AlwaysFlat(), enable_trading=True)

    decision = trader.step(bars)

    assert decision.intended_orders == []
    assert "inside rebalance band" in decision.note


def test_every_cycle_is_journalled_for_reconciliation(tmp_path):
    journal = tmp_path / "journal.jsonl"
    bars, _, trader = _trader(journal_path=journal)

    trader.step(bars)
    trader.step(bars)

    lines = journal.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert {"ts", "equity", "targets", "intended_orders", "note"} <= record.keys()


def test_daily_order_cap_resets_on_a_new_day():
    bars, broker, trader = _trader(
        enable_trading=True,
        limits=RiskLimits(max_position_notional=1e9, max_orders_per_day=1),
    )
    trader.strategy = Flipping()  # keep generating orders to hit the cap

    trader.step(bars, now=pd.Timestamp("2026-08-04 14:00", tz="UTC"))
    blocked = trader.step(bars, now=pd.Timestamp("2026-08-04 15:00", tz="UTC"))
    assert "BLOCKED" in blocked.note

    fresh = trader.step(bars, now=pd.Timestamp("2026-08-05 14:00", tz="UTC"))
    assert "BLOCKED" not in fresh.note


def test_alpaca_adapter_refuses_a_non_paper_endpoint():
    from stocklab.execution.alpaca import AlpacaPaperBroker

    with pytest.raises(ValueError, match="paper"):
        AlpacaPaperBroker("key", "secret", base_url="https://api.alpaca.markets")
