"""Tests for the intraday path.

The risk here is not a wrong number on a chart — it is intraday bars leaking
into something calibrated for daily bars and producing a plausible-looking
result that is nonsense.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stocklab.data.intraday import INTERVALS, KEEP_BARS, IntradaySource


def _frame(n: int, start: str = "2026-08-03 13:30", freq: str = "1min") -> pd.DataFrame:
    index = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100.0},
        index=index,
    )


def test_unsupported_interval_is_rejected_loudly():
    with pytest.raises(ValueError, match="unsupported interval"):
        IntradaySource(cache_dir=None).fetch(["AAA"], interval="3m")


def test_only_the_retained_tail_is_returned(monkeypatch, tmp_path):
    source = IntradaySource(cache_dir=tmp_path)
    monkeypatch.setattr(
        IntradaySource, "_download", lambda self, symbol, interval: _frame(1000)
    )

    bars = source.fetch(["AAA"], "1m")

    assert len(bars["AAA"]) == KEEP_BARS["1m"]
    assert bars["AAA"].index.is_monotonic_increasing


def test_a_download_failure_falls_back_to_cache(monkeypatch, tmp_path):
    """An intraday outage must not cost you the rest of the dashboard."""
    source = IntradaySource(cache_dir=tmp_path)
    _frame(100).to_parquet(tmp_path / "AAA_5m.parquet")

    monkeypatch.setattr(
        IntradaySource,
        "_download",
        lambda self, symbol, interval: (_ for _ in ()).throw(ValueError("network down")),
    )

    bars = source.fetch(["AAA"], "5m")

    assert "AAA" in bars and not bars["AAA"].empty
    assert "served from cache" in source.failures["AAA"]


def test_a_failure_with_no_cache_is_reported_and_skipped(monkeypatch, tmp_path):
    source = IntradaySource(cache_dir=tmp_path)
    monkeypatch.setattr(
        IntradaySource,
        "_download",
        lambda self, symbol, interval: (_ for _ in ()).throw(ValueError("no data")),
    )

    bars = source.fetch(["AAA"], "5m")

    assert bars == {}
    assert "AAA" in source.failures


def test_every_declared_interval_has_a_retention_setting():
    assert set(INTERVALS) == set(KEEP_BARS)


def test_intraday_is_not_reachable_from_the_backtest_engine():
    """Guards the boundary: costs and every validated result assume daily bars.

    If someone wires IntradaySource into the engine, the numbers keep looking
    like backtests while silently becoming meaningless — slippage and fees
    alone are wrong by an order of magnitude at this frequency.
    """
    import inspect

    from stocklab.backtest import engine, walkforward

    for module in (engine, walkforward):
        source = inspect.getsource(module)
        assert "intraday" not in source.lower(), f"{module.__name__} references intraday data"
