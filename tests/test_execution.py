"""Tests for the live path.

Every safety layer is tested to *not* trade, because the expensive failure in
this module is an order that should never have left the process.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from stocklab.backtest.costs import FLAT_DEFAULT, CostModel
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


def _allocate_fully(costs: CostModel, cash_buffer: float) -> float:
    """Cash left after putting 100% of equity to work. Negative = overdrawn."""
    price = 250.0
    broker = SimulatedBroker(cash=100_000.0, costs=costs)
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

    remaining = _allocate_fully(FLAT_DEFAULT, cash_buffer=DEFAULT_CASH_BUFFER)
    assert remaining >= 0.0, f"overdrawn by {-remaining:.2f}"


def test_the_buffer_must_exceed_round_trip_cost():
    """The documented invariant, asserted rather than trusted.

    A buffer smaller than slippage + fees still overdraws — this pins the
    relationship so nobody raises costs later without raising the buffer.
    """
    expensive = CostModel(name="expensive", commission_bps=10.0, slippage_bps=50.0)
    assert _allocate_fully(expensive, cash_buffer=0.002) < 0.0
    assert _allocate_fully(expensive, cash_buffer=0.01) >= 0.0


def test_the_cost_model_sizes_its_own_buffer():
    """A model that knows it is expensive must ask for a wider buffer.

    The constant in `sizing` is a floor, not an answer: raising costs without
    raising the buffer is exactly the mistake this pins down.
    """
    expensive = CostModel(name="expensive", commission_bps=10.0, slippage_bps=50.0)
    assert _allocate_fully(expensive, cash_buffer=expensive.min_cash_buffer()) >= 0.0


def test_sizing_refuses_to_act_on_worthless_account():
    assert compute_orders({"AAA": 1.0}, {}, {"AAA": 10.0}, equity=0.0) == []


def test_backtest_and_live_share_one_sizing_path():
    """Parity is the point of the shared module — assert it explicitly."""
    from stocklab.backtest import engine

    assert engine.compute_orders is compute_orders


# --------------------------------------------------------------- broker


def test_simulated_broker_applies_costs_against_you():
    broker = SimulatedBroker(
        cash=1000.0,
        costs=CostModel(name="test", commission_bps=10.0, slippage_bps=100.0),
    )
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


def test_dry_run_records_a_breach_instead_of_aborting_on_it():
    """The bug this pins cost twelve days of paper-trading record.

    With a $1,000 notional cap against a $100,000 account, every order the rule
    wanted breached the limit. The check ran before the `enable_trading` branch,
    so each day journalled one opaque `BLOCKED` line and threw away the orders
    it had just computed — the exact thing weeks of dry run exist to collect.

    No order can leave the process in dry run either way, so the breach is
    information, not a reason to stop.
    """
    bars, broker, trader = _trader(limits=RiskLimits(max_position_notional=100.0))

    decision = trader.step(bars)

    assert broker.submitted == [], "dry run must never submit, breach or not"
    assert decision.traded is False
    assert decision.intended_orders, "the record of what the rule wanted must survive"
    assert "DRY RUN" in decision.note
    assert "would have been blocked" in decision.note
    assert "notional" in decision.note, "and which limit it was"


def test_a_breach_still_aborts_a_live_cycle():
    """The safety property itself, unchanged: live trading stops on a breach."""
    bars, broker, trader = _trader(
        enable_trading=True, limits=RiskLimits(max_position_notional=100.0)
    )

    decision = trader.step(bars)

    assert broker.submitted == []
    assert decision.traded is False
    assert decision.note.startswith("BLOCKED")


def test_equity_sized_limits_permit_a_full_allocation():
    """The invariant the twelve wasted days violated.

    A strategy that wants 100% of equity in one name is doing what it was told.
    If the notional cap cannot accommodate that, every cycle breaches and the
    cap has become an off switch rather than a circuit breaker.
    """
    limits = RiskLimits.for_equity(100_000.0)
    full_allocation = Order(symbol="SPY", side="buy", qty=100_000.0 / 500.0)

    limits.check(full_allocation, price=500.0, orders_today=0, pnl_today=0.0)


def test_equity_sized_limits_still_refuse_a_leveraged_order():
    """And it must remain a circuit breaker: 2x equity is a bug, not a trade."""
    limits = RiskLimits.for_equity(100_000.0)
    leveraged = Order(symbol="SPY", side="buy", qty=2 * 100_000.0 / 500.0)

    with pytest.raises(RuntimeError, match="notional"):
        limits.check(leveraged, price=500.0, orders_today=0, pnl_today=0.0)


def test_equity_sized_daily_loss_tolerates_an_ordinary_red_day():
    """The default $100 limit would halt a $100k account on a 0.1% dip.

    Sized as a fraction, a normal down day passes and a genuinely bad one does
    not — which is the distinction the limit exists to draw.
    """
    limits = RiskLimits.for_equity(100_000.0)
    order = Order(symbol="SPY", side="buy", qty=1.0)

    limits.check(order, price=500.0, orders_today=0, pnl_today=-900.0)

    with pytest.raises(RuntimeError, match="daily loss"):
        limits.check(order, price=500.0, orders_today=0, pnl_today=-2_500.0)


def test_sizing_limits_needs_a_real_account():
    with pytest.raises(ValueError, match="positive"):
        RiskLimits.for_equity(0.0)


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


def test_an_unreachable_broker_blocks_rather_than_looking_idle():
    """The failure this exists to prevent.

    A gateway that is down and a strategy that wants no change produce the same
    empty order list. Journalled identically, nobody can tell weeks later which
    happened — so the loop must refuse to decide at all.
    """
    bars, broker, trader = _trader(
        enable_trading=True, limits=RiskLimits(max_position_notional=1e9)
    )
    broker.set_ready(False)

    decision = trader.step(bars)

    assert decision.traded is False
    assert broker.submitted == []
    assert "BLOCKED" in decision.note and "not ready" in decision.note
    assert decision.targets == {}, "must not consult the strategy either"


def test_a_whole_share_broker_never_gets_a_fractional_order():
    bars = make_bars(n_bars=60, symbols=("AAA",))
    price = float(bars["AAA"]["close"].iloc[-1])
    broker = SimulatedBroker(cash=10_000.0, qty_increment=1.0)
    broker.set_prices({"AAA": price})
    trader = Trader(
        broker=broker,
        strategy=AlwaysLong(),
        enable_trading=True,
        limits=RiskLimits(max_position_notional=1e9),
    )

    trader.step(bars)

    assert broker.submitted
    for order in broker.submitted:
        assert order.qty == int(order.qty), f"{order.qty} is not a whole share"


def test_alpaca_adapter_refuses_a_non_paper_endpoint():
    from stocklab.execution.alpaca import AlpacaPaperBroker

    with pytest.raises(ValueError, match="paper"):
        AlpacaPaperBroker("key", "secret", base_url="https://api.alpaca.markets")
