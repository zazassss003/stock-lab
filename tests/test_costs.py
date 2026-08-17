"""Tests for the cost model.

These pin down the parts of a fee schedule that are easy to get subtly wrong
and impossible to notice afterwards: a floor applied without its cap, a
regulatory fee charged on the wrong side, a currency conversion that quietly
never happens. Each one flatters a backtest, which is why each one gets a test.
"""

from __future__ import annotations

import pytest

from stocklab.backtest import engine
from stocklab.backtest.costs import (
    FIRSTRADE,
    FLAT_DEFAULT,
    IBKR_PRO,
    SEC_FEE_PER_MILLION,
    SUB_BROKERAGE_TW,
    TAF_MAX_PER_TRADE,
    TAF_PER_SHARE,
    ZERO_COST_FOR_DEBUGGING,
    CostModel,
)

from conftest import make_bars


class AlwaysLong:
    def on_bar(self, history):
        return {"AAA": 1.0}


class Flipping:
    """In and out every bar, so costs accumulate rather than being paid once."""

    def __init__(self) -> None:
        self._long = False

    def on_bar(self, history):
        self._long = not self._long
        return {"AAA": 1.0 if self._long else 0.0}


# --------------------------------------------------------------- commission


def test_the_minimum_ticket_dominates_a_small_order():
    """$0.005/share reads as nothing until you buy ten shares of anything."""
    ten_shares = IBKR_PRO.commission(10, 250.0)

    assert ten_shares == pytest.approx(1.0)  # not 10 * 0.005 = $0.05
    assert IBKR_PRO.commission(1_000, 250.0) == pytest.approx(5.0)


def test_the_cap_beats_the_floor_on_a_tiny_notional():
    """Ordering matters: the 1% cap must be applied *after* the $1 minimum.

    A single $40 share owes $0.40, not $1. Applying the floor last would
    overcharge every small order — a 150% error on this trade.
    """
    assert IBKR_PRO.commission(1, 40.0) == pytest.approx(0.40)


def test_percentage_commission_scales_with_size():
    assert SUB_BROKERAGE_TW.commission(10, 100.0) == pytest.approx(1.0)  # 0.1%
    assert SUB_BROKERAGE_TW.commission(100, 100.0) == pytest.approx(10.0)


def test_a_commission_free_broker_charges_no_commission():
    assert FIRSTRADE.commission(100, 250.0) == 0.0


# --------------------------------------------------------------- regulatory


def test_regulatory_fees_are_charged_on_sells_only():
    """Both the SEC and FINRA fees fall on the seller. A buy owes neither."""
    assert FLAT_DEFAULT.regulatory(shares=100, price=250.0) == 0.0
    assert FLAT_DEFAULT.regulatory(shares=-100, price=250.0) > 0.0


def test_regulatory_fee_matches_the_published_rates():
    proceeds = 100 * 250.0
    expected = proceeds * SEC_FEE_PER_MILLION / 1e6 + 100 * TAF_PER_SHARE

    assert FLAT_DEFAULT.regulatory(-100, 250.0) == pytest.approx(expected)


def test_the_finra_fee_is_capped_per_trade():
    """Without the cap a million-share sell would be charged $166."""
    huge = FLAT_DEFAULT.regulatory(-1_000_000, 1.0)
    sec_part = 1_000_000 * 1.0 * SEC_FEE_PER_MILLION / 1e6

    assert huge - sec_part == pytest.approx(TAF_MAX_PER_TRADE)


def test_regulatory_fees_can_be_switched_off_but_are_on_by_default():
    assert CostModel(name="x").regulatory_fees is True
    assert CostModel(name="x", regulatory_fees=False).regulatory(-100, 250.0) == 0.0


# --------------------------------------------------------------- slippage


def test_slippage_always_works_against_the_trader():
    model = CostModel(name="x", slippage_bps=10.0)

    assert model.fill_price(100.0, shares=1) == pytest.approx(100.10)  # pay up
    assert model.fill_price(100.0, shares=-1) == pytest.approx(99.90)  # sell down


# --------------------------------------------------------------- currency


def test_currency_costs_are_charged_in_both_directions():
    """A round trip pays the spread twice — once in, once out."""
    assert FIRSTRADE.fx_cost(20_000.0) == pytest.approx(45.0 + 20_000 * 0.0030)


def test_the_broker_with_the_cheaper_wire_can_lose_on_currency():
    """The comparison the fee tables leave out.

    Firstrade charges no commission and IBKR charges a $1 minimum, yet on a
    single $20,000 funding transfer the retail FX spread costs more than a
    year of IBKR minimum tickets.
    """
    transfer = 20_000.0

    assert FIRSTRADE.fx_cost(transfer) > IBKR_PRO.fx_cost(transfer) + 100.0


def test_a_model_that_ignores_currency_charges_nothing_for_it():
    assert FLAT_DEFAULT.fx_cost(20_000.0) == 0.0
    assert IBKR_PRO.without_fx().fx_cost(20_000.0) == 0.0


# --------------------------------------------------------------- guards


def test_a_cost_model_must_be_named():
    with pytest.raises(ValueError, match="named"):
        CostModel(name="")


def test_negative_costs_are_rejected():
    with pytest.raises(ValueError, match="negative"):
        CostModel(name="rebate", commission_bps=-1.0)


def test_the_only_free_model_says_so_in_its_name():
    """A zero-cost run must be greppable, because it is never a result."""
    assert ZERO_COST_FOR_DEBUGGING.is_free
    assert "ZERO COST" in ZERO_COST_FOR_DEBUGGING.name
    assert not FLAT_DEFAULT.is_free


# --------------------------------------------------------------- in the engine


def test_a_more_expensive_broker_ends_with_less_money():
    bars = make_bars(n_bars=120, symbols=("AAA",))

    cheap = engine.run(bars, Flipping(), costs=CostModel(name="cheap", commission_bps=1.0))
    dear = engine.run(bars, Flipping(), costs=CostModel(name="dear", commission_bps=50.0))

    assert dear.equity.iloc[-1] < cheap.equity.iloc[-1]
    assert dear.stats["total_commission"] > cheap.stats["total_commission"]


def test_every_cost_layer_shows_up_separately_in_the_stats():
    """A single "fees" number hides which layer actually hurt."""
    bars = make_bars(n_bars=120, symbols=("AAA",))
    stats = engine.run(bars, Flipping(), costs=IBKR_PRO).stats

    assert stats["total_commission"] > 0.0
    assert stats["total_regulatory"] > 0.0  # something was sold
    assert stats["total_slippage"] > 0.0
    assert stats["fx_cost"] > 0.0
    assert stats["total_fees"] == pytest.approx(
        stats["total_commission"] + stats["total_regulatory"]
    )


def test_funding_the_account_costs_money_before_the_first_trade():
    """The conversion happens whether or not anything is ever bought."""
    bars = make_bars(n_bars=30, symbols=("AAA",))

    class DoNothing:
        def on_bar(self, history):
            return {}

    result = engine.run(bars, DoNothing(), initial_cash=100_000.0, costs=FIRSTRADE)

    assert not result.fills
    assert result.equity.iloc[0] < 100_000.0
    assert result.stats["fx_cost"] > 0.0


def test_net_return_is_measured_against_money_committed():
    """`total_return` is what the account did; `net_return` is what you got.

    They differ by the cost of getting the money in and out, which is invisible
    inside the account and is the whole point of modelling currency.
    """
    bars = make_bars(n_bars=120, symbols=("AAA",))
    result = engine.run(bars, AlwaysLong(), costs=FIRSTRADE)

    assert result.stats["net_return"] < result.stats["total_return"]


def test_without_currency_the_two_returns_agree():
    bars = make_bars(n_bars=120, symbols=("AAA",))
    stats = engine.run(bars, AlwaysLong(), costs=FLAT_DEFAULT).stats

    assert stats["net_return"] == pytest.approx(stats["total_return"])


def test_a_sliced_window_is_not_charged_for_a_conversion():
    """Walk-forward scores windows in the middle of history.

    No money crossed a currency line there, so charging that window for a wire
    would be invention — and would make every fold look worse than it was.
    """
    from stocklab.backtest.walkforward import slice_result

    bars = make_bars(n_bars=120, symbols=("AAA",))
    result = engine.run(bars, AlwaysLong(), costs=FIRSTRADE)
    index = bars["AAA"].index

    window = slice_result(result, index[40], index[80])

    assert window.gross_capital is None
    assert window.stats["fx_cost"] == 0.0
    assert window.stats["net_return"] == pytest.approx(window.stats["total_return"])


def test_whole_share_trading_never_produces_a_fractional_position():
    bars = make_bars(n_bars=120, symbols=("AAA",))

    result = engine.run(bars, Flipping(), costs=IBKR_PRO, qty_increment=1.0)

    assert result.fills
    for fill in result.fills:
        assert fill.qty == int(fill.qty), f"{fill.qty} is not a whole share"


def test_fractional_trading_is_still_the_default():
    bars = make_bars(n_bars=120, symbols=("AAA",))

    result = engine.run(bars, Flipping(), costs=IBKR_PRO)

    assert any(fill.qty != int(fill.qty) for fill in result.fills)


def test_the_engine_reports_which_cost_model_produced_the_numbers():
    bars = make_bars(n_bars=30, symbols=("AAA",))

    assert engine.run(bars, AlwaysLong(), costs=IBKR_PRO).costs.name == IBKR_PRO.name
