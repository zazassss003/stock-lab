"""The research package: its boundary, and the two calculations that lie quietly.

The boundary test is the important one. `research/` is the only place a language
model's output is displayed, and rule 2 holds because nothing on the trade path
can reach it. That is currently true by convention, and a convention nobody
checks is a convention that lasts until the first convenient import. This makes
it fail the build instead.

The staleness and excess-return tests exist because both fail *silently*. A
broken staleness rule shows a confident six-week-old note with no warning; a
broken excess return shows a name beating the index when it is losing to it.
Neither raises, so neither gets noticed without a test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pandas as pd
import pytest

from stocklab.research.context import build_context
from stocklab.research.notes import STALE_DAYS, STALE_DRIFT, Note

from conftest import make_bars

SRC = Path(__file__).resolve().parents[1] / "src" / "stocklab"

# The trade path. Anything here that imports `research` breaks rule 2, because
# it opens a route from a sentence a model wrote to an order.
TRADE_PATH = ("strategy", "backtest", "execution", "features", "data")


def _imported_modules(path: Path) -> set[str]:
    """Every module name imported by a file, absolute and relative alike."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from ..research import x` has module="research", and level>0
            # marks it relative — both spellings have to be caught.
            if node.module:
                names.add(node.module)
            names.update(alias.name for alias in node.names)

    return names


@pytest.mark.parametrize("package", TRADE_PATH)
def test_trade_path_does_not_import_research(package: str) -> None:
    for file in sorted((SRC / package).rglob("*.py")):
        offenders = {name for name in _imported_modules(file) if "research" in name}
        assert not offenders, (
            f"{file.relative_to(SRC)} imports {sorted(offenders)}. "
            "research/ is a dead end by design — see rule 2 in CLAUDE.md."
        )


def _note(close_at_write: float = 100.0, written: str = "2026-01-01T00:00:00+00:00") -> Note:
    return Note(
        symbol="AAA",
        headline="h",
        thesis="t",
        written=written,
        source="test",
        close_at_write=close_at_write,
        risks=["r1", "r2"],
    )


def test_fresh_note_is_not_stale() -> None:
    today = pd.Timestamp("2026-01-10T00:00:00+00:00")
    assert _note().staleness(close_now=101.0, today=today) is None


def test_price_drift_marks_a_note_stale_in_both_directions() -> None:
    today = pd.Timestamp("2026-01-10T00:00:00+00:00")
    note = _note()

    # A note is equally wrong about a stock that ran away from it as one that
    # collapsed under it, so the rule is on absolute move.
    assert note.staleness(100.0 * (1 + STALE_DRIFT), today) is not None
    assert note.staleness(100.0 * (1 - STALE_DRIFT), today) is not None
    assert note.staleness(100.0 * (1 + STALE_DRIFT * 0.9), today) is None


def test_age_marks_a_note_stale_even_when_the_price_has_not_moved() -> None:
    written = pd.Timestamp("2026-01-01T00:00:00+00:00")
    note = _note(written=written.isoformat())

    just_under = written + pd.Timedelta(days=STALE_DAYS - 1)
    just_over = written + pd.Timedelta(days=STALE_DAYS)

    assert note.staleness(100.0, just_under) is None
    assert note.staleness(100.0, just_over) is not None


def test_excess_return_is_relative_to_the_benchmark() -> None:
    bars = make_bars(n_bars=300, symbols=("AAA", "SPY"), seed=3)
    context = build_context("AAA", bars["AAA"], bars["SPY"])

    own = bars["AAA"]["close"]
    bench = bars["SPY"]["close"]
    expected = (own.iloc[-1] / own.iloc[-253] - 1) - (bench.iloc[-1] / bench.iloc[-253] - 1)

    assert context.excess_1y == pytest.approx(expected)


def test_excess_return_is_none_rather_than_absolute_without_a_benchmark() -> None:
    """The dangerous failure: reporting absolute return in a relative column.

    In a rising market that flatters every name on the page, which is exactly
    the illusion the vs-SPY column exists to puncture.
    """
    bars = make_bars(n_bars=300, symbols=("AAA",), seed=4)
    context = build_context("AAA", bars["AAA"], benchmark=None)

    assert context.excess_1y is None
    assert context.chg_1y is not None


def test_benchmark_has_no_excess_against_itself() -> None:
    bars = make_bars(n_bars=300, symbols=("SPY",), seed=5)
    context = build_context("SPY", bars["SPY"], bars["SPY"])

    assert context.excess_1y is None


def test_short_history_yields_none_not_a_wrong_number() -> None:
    bars = make_bars(n_bars=30, symbols=("AAA", "SPY"), seed=6)
    context = build_context("AAA", bars["AAA"], bars["SPY"])

    assert context.chg_1y is None
    assert context.excess_1y is None
    assert context.chg_1m is not None
