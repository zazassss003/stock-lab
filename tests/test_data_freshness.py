"""Tests for keeping a tracker current without corrupting it.

The failure these guard against is subtle: an in-progress session looks like a
normal bar, so a strategy acts on a "close" that is still moving, and the
signal changes retroactively once the session actually closes.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stocklab.data.yfinance_source import drop_incomplete_session, drop_unpriced, merge_bars

from conftest import make_bars


def _frame(dates: list[str]) -> pd.DataFrame:
    index = pd.DatetimeIndex([pd.Timestamp(d, tz="UTC") for d in dates])
    return pd.DataFrame(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100.0},
        index=index,
    )


def test_todays_bar_is_dropped_before_the_close():
    df = _frame(["2026-08-03", "2026-08-04"])
    now = pd.Timestamp("2026-08-04 11:30", tz="America/New_York")  # mid-session

    kept = drop_incomplete_session(df, now=now)

    assert len(kept) == 1
    assert kept.index[-1].date() == pd.Timestamp("2026-08-03").date()


def test_todays_bar_is_kept_after_the_close():
    df = _frame(["2026-08-03", "2026-08-04"])
    now = pd.Timestamp("2026-08-04 16:30", tz="America/New_York")

    assert len(drop_incomplete_session(df, now=now)) == 2


def test_yesterdays_final_bar_is_never_dropped():
    """Before the open, the newest bar is a completed prior session."""
    df = _frame(["2026-08-03"])
    now = pd.Timestamp("2026-08-04 08:00", tz="America/New_York")

    assert len(drop_incomplete_session(df, now=now)) == 1


def test_unpriced_rows_are_dropped_and_reported():
    """Yahoo ships the newest bar's volume before its adjusted prices."""
    df = _frame(["2026-08-01", "2026-08-03"])
    df.loc[df.index[-1], ["open", "high", "low", "close"]] = float("nan")
    df.loc[df.index[-1], "volume"] = 51_941_340.0  # volume still populated

    clean, dropped = drop_unpriced(df)

    assert len(clean) == 1
    assert clean.index[-1].date() == pd.Timestamp("2026-08-01").date()
    assert [ts.date() for ts in dropped] == [pd.Timestamp("2026-08-03").date()]


def test_fully_priced_rows_are_never_dropped():
    df = _frame(["2026-08-01", "2026-08-03"])
    clean, dropped = drop_unpriced(df)

    assert len(clean) == 2
    assert dropped == []


def test_merge_prefers_freshly_downloaded_bars_on_overlap():
    """Providers revise recent bars; the re-fetched tail must win."""
    cached = _frame(["2026-08-03", "2026-08-04"])
    fresh = _frame(["2026-08-04", "2026-08-05"])
    fresh.loc[:, "close"] = 99.0

    merged = merge_bars(cached, fresh)

    assert list(merged.index.strftime("%Y-%m-%d")) == ["2026-08-03", "2026-08-04", "2026-08-05"]
    assert merged.loc[pd.Timestamp("2026-08-04", tz="UTC"), "close"] == 99.0  # revised
    assert merged.loc[pd.Timestamp("2026-08-03", tz="UTC"), "close"] == 1.5  # untouched
    assert merged.index.is_monotonic_increasing
    assert not merged.index.has_duplicates


def test_merge_handles_an_empty_cache():
    fresh = _frame(["2026-08-03"])
    assert merge_bars(None, fresh).equals(fresh)
    assert merge_bars(_frame([]).iloc[:0], fresh).equals(fresh)


def test_pipeline_reports_staleness(monkeypatch, tmp_path):
    """A stale feed must be surfaced, not silently rendered as if current."""
    from stocklab import pipeline

    bars = make_bars(n_bars=80)  # synthetic, ends well before today
    monkeypatch.setattr(
        pipeline.YFinanceSource, "fetch", lambda self, symbols: {symbols[0]: bars["AAA"]}
    )

    info = pipeline.refresh_and_report("AAA", tmp_path / "out.html")

    assert info["stale"] is True
    assert info["age_days"] > pipeline.STALE_AFTER_DAYS
    assert (tmp_path / "out.html").exists()


def test_pipeline_is_not_stale_for_current_data(monkeypatch, tmp_path):
    from stocklab import pipeline

    bars = make_bars(n_bars=80)
    today = pd.Timestamp.now(tz="UTC").normalize()
    shifted = bars["AAA"].copy()
    shifted.index = pd.date_range(end=today, periods=len(shifted), freq="B", tz="UTC")

    monkeypatch.setattr(
        pipeline.YFinanceSource, "fetch", lambda self, symbols: {symbols[0]: shifted}
    )

    info = pipeline.refresh_and_report("AAA", tmp_path / "out.html")

    assert info["stale"] is False
    assert info["age_days"] == 0
