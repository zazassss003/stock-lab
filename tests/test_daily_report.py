"""Tests for the daily brief.

This is the one artefact read every single day without thinking, so the risks
are (a) it quotes a threshold the rule does not actually use, and (b) it says
nothing changed when something did.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocklab.report.daily import (
    SignalLine,
    build_report,
    donchian_levels,
    explain_donchian,
    load_previous,
    save_state,
)
from stocklab.strategy.donchian import DonchianBreakout

from conftest import make_bars


def _report(signals, previous, **overrides):
    kwargs = dict(
        data_age_days=1,
        last_bar="2026-08-04",
        symbols_updated=len(signals),
        stale_symbols=[],
        unpriced={},
        trading_enabled=False,
        halted=False,
        strategy_label="Donchian 55/20 (not validated)",
        validated=False,
        today=date(2026, 8, 5),
    )
    kwargs.update(overrides)
    return build_report(signals, previous, **kwargs)


LONG = SignalLine("NVDA", 1.0, 200.0, "exits below 176.40 (20d low), 13.4% away")
FLAT = SignalLine("SPY", 0.0, 632.0, "enters above 641.10 (55d high), 1.4% away")


# --------------------------------------------------- the levels must match the rule


def test_reported_levels_are_the_ones_the_strategy_actually_uses():
    """A brief quoting different thresholds than the rule is worse than none."""
    frame = make_bars(n_bars=200, symbols=("AAA",))["AAA"]
    entry, exit = 55, 20

    reported_high, reported_low = donchian_levels(frame, entry, exit)

    # Recompute the way DonchianBreakout.on_bar does, on the next bar's view.
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    assert reported_high == pytest.approx(highs[-entry:].max())
    assert reported_low == pytest.approx(lows[-exit:].min())


def test_explanation_points_the_right_direction():
    frame = make_bars(n_bars=200, symbols=("AAA",))["AAA"]

    assert "enters above" in explain_donchian(frame, 0.0, 55, 20)
    assert "exits below" in explain_donchian(frame, 1.0, 55, 20)


def test_explanation_matches_the_live_strategy_position():
    """Replaying the rule and asking the report for a reason must agree."""
    frame = make_bars(n_bars=200, symbols=("AAA",))["AAA"]
    strategy = DonchianBreakout("AAA", 55, 20)

    target = 0.0
    for i in range(len(frame)):
        target = strategy.on_bar({"AAA": frame.iloc[: i + 1]}).get("AAA", 0.0)

    reason = explain_donchian(frame, target, 55, 20)
    assert ("exits below" in reason) == (target > 0)


# --------------------------------------------------- change detection


def test_a_position_flip_is_reported_as_the_headline():
    report = _report([LONG], {"NVDA": 0.0})

    assert "flat -> LONG" in report
    assert "Nothing." not in report


def test_no_change_says_so_plainly():
    report = _report([LONG, FLAT], {"NVDA": 1.0, "SPY": 0.0})

    assert "Nothing. No position would have changed today." in report


def test_first_run_does_not_pretend_everything_changed():
    report = _report([LONG, FLAT], {})

    assert "First run" in report
    assert "->" not in report.split("POSITIONS")[0]


def test_a_newly_tracked_symbol_is_called_out_not_shown_as_a_flip():
    report = _report([LONG, FLAT], {"NVDA": 1.0})

    assert "Now tracking: SPY" in report
    assert "flat -> " not in report


# --------------------------------------------------- standing state


def test_unvalidated_strategy_is_disclaimed_every_single_day():
    assert "not a recommendation" in _report([LONG], {"NVDA": 1.0})


def test_validated_strategy_drops_the_disclaimer():
    assert "not a recommendation" not in _report([LONG], {"NVDA": 1.0}, validated=True)


def test_kill_switch_is_the_first_thing_in_status():
    report = _report([LONG], {"NVDA": 1.0}, halted=True)
    status = report.split("STATUS")[1]

    assert status.strip().startswith("HALTED")


def test_live_trading_is_stated_explicitly():
    assert "Trading ENABLED" in _report([LONG], {"NVDA": 1.0}, trading_enabled=True)


def test_stale_data_is_surfaced_not_buried():
    report = _report([LONG], {"NVDA": 1.0}, data_age_days=9, stale_symbols=["NVDA"])

    assert "CHECK" in report
    assert "Stale: NVDA" in report


def test_dropped_sessions_are_reported():
    report = _report([LONG], {"NVDA": 1.0}, unpriced={"NVDA": ["2026-08-03"]})

    assert "Dropped unpriced sessions" in report


def test_the_report_is_pure_ascii():
    """cp950 is this machine's default: one stray typographic dash and the
    scheduled run fails mid-pipe, or the file reads as mojibake."""
    report = _report([LONG, FLAT], {"NVDA": 0.0}, halted=True, stale_symbols=["SPY"],
                     unpriced={"SPY": ["2026-08-03"]})

    report.encode("ascii")  # raises if anything typographic slipped in


def test_the_brief_stays_brief():
    """Twelve symbols must still fit on one screen, or it stops being read."""
    many = [SignalLine(f"S{i}", float(i % 2), 100.0 + i, "enters above 1.00 (55d high), 1.0% away")
            for i in range(12)]

    assert len(_report(many, {}).splitlines()) < 40


# --------------------------------------------------- state round-trip


def test_state_round_trips(tmp_path):
    path = tmp_path / "state.json"
    save_state(path, [LONG, FLAT])

    assert load_previous(path) == {"NVDA": 1.0, "SPY": 0.0}


def test_a_corrupt_state_file_does_not_break_the_run(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")

    assert load_previous(path) == {}


def test_missing_state_file_is_treated_as_a_first_run(tmp_path):
    assert load_previous(tmp_path / "nope.json") == {}
