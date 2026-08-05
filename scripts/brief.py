"""Build the research briefing page for the tracked list.

    py -3 scripts/brief.py              # render research.html from the note store
    py -3 scripts/brief.py --prompts    # print the prompt pack for the skills
    py -3 scripts/brief.py NVDA AAPL    # restrict to some symbols

The two-step shape is deliberate. `--prompts` emits one block per symbol,
pre-loaded with that symbol's deterministic numbers, to hand to the
equity-research skills. The agent writes its answers into
`research_store/notes.json`. This script then renders. Generation and rendering
are separate processes on purpose: it keeps the model's output a reviewable file
on disk rather than something that appears directly on a page nobody audited.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stocklab.data.yfinance_source import YFinanceSource  # noqa: E402
from stocklab.pipeline import CACHE_DIR, STALE_AFTER_DAYS  # noqa: E402
from stocklab.research.context import build_all  # noqa: E402
from stocklab.research.notes import load_notes  # noqa: E402
from stocklab.research.page import build_payload, render_briefing  # noqa: E402
from stocklab.universe import SYMBOLS  # noqa: E402

STORE = ROOT / "research_store" / "notes.json"
OUTPUT = ROOT / "research.html"

# ASCII only. This block gets printed to a console that may be cp950 or cp1252,
# where an em-dash comes out as mojibake and makes the prompt look corrupted.
PROMPT = """\
=== {symbol} | {name} ({sector}) ===
Deterministic context, bars through {last_bar} (do not recompute these; they are
authoritative and any figure you produce that contradicts them is wrong):

  last close     {close:.2f}
  1d / 1m / 3m   {chg_1d} / {chg_1m} / {chg_3m}
  1y             {chg_1y}   (vs SPY: {excess_1y})
  52w range      {low_52w:.2f} - {high_52w:.2f}   now {from_high} from the high
  RSI(14)        {rsi}      20d realised vol {vol_20d}
  trend          20/50 SMA {trend}

Run: /earnings {symbol} latest quarter
Then write a note as JSON matching stocklab.research.notes.Note, with fields:
  symbol, headline, thesis, written (ISO8601 UTC), source, close_at_write={close:.2f},
  risks (>=2, each one a thing that would falsify the thesis), catalysts
  (dated, forward-looking only), watch (metrics to check next quarter).
Append it to research_store/notes.json under the "notes" key, keyed by symbol.
"""


def _fmt(value: float | None, digits: int = 1) -> str:
    """Signed percentage — for returns, where direction is the point."""
    return "n/a" if value is None else f"{value * 100:+.{digits}f}%"


def _plain(value: float | None, digits: int = 0) -> str:
    """Unsigned percentage. Volatility has no direction; a `+` implies one."""
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def main() -> None:
    args = sys.argv[1:]
    want_prompts = "--prompts" in args
    symbols = [a for a in args if not a.startswith("-")] or SYMBOLS

    source = YFinanceSource(cache_dir=CACHE_DIR)
    frames: dict = {}
    failures: dict[str, str] = {}

    # SPY is always fetched: it is the benchmark leg for every excess return,
    # and without it the "vs SPY" column silently becomes n/a for everything.
    for symbol in dict.fromkeys([*symbols, "SPY"]):
        try:
            frames[symbol] = source.fetch([symbol])[symbol]
        except Exception as error:
            failures[symbol] = str(error)

    if not frames:
        raise SystemExit(f"no symbols could be fetched: {failures}")

    contexts = build_all(frames)

    if want_prompts:
        for symbol in symbols:
            context = contexts.get(symbol)
            if context is None or symbol == "SPY":
                continue
            print(
                PROMPT.format(
                    **{
                        **context.to_dict(),
                        "chg_1d": _fmt(context.chg_1d),
                        "chg_1m": _fmt(context.chg_1m),
                        "chg_3m": _fmt(context.chg_3m),
                        "chg_1y": _fmt(context.chg_1y),
                        "excess_1y": _fmt(context.excess_1y),
                        "from_high": _fmt(context.from_high),
                        "rsi": "n/a" if context.rsi is None else f"{context.rsi:.0f}",
                        "vol_20d": _plain(context.vol_20d),
                        "trend": context.trend or "n/a",
                    }
                )
            )
        return

    notes = load_notes(STORE)
    payload = build_payload(contexts, notes, symbols)
    path = render_briefing(payload, OUTPUT)

    size_kb = path.stat().st_size / 1024
    print(f"wrote {path} ({size_kb:,.0f} KB)")
    print(f"  {payload['covered']}/{payload['total']} symbols have a note")
    print(f"  prices through {payload['last_bar']}")

    stale = [
        row["context"]["symbol"]
        for row in payload["rows"]
        if row["note"] and row["note"]["stale_reason"]
    ]
    missing = [row["context"]["symbol"] for row in payload["rows"] if not row["note"]]

    if stale:
        print(f"  stale notes: {', '.join(stale)}")
    if missing:
        print(f"  no note: {', '.join(missing)}")
    if failures:
        print(f"  FAILED: {failures}")

    import pandas as pd

    age = (pd.Timestamp.now(tz="UTC").normalize() - pd.Timestamp(payload["last_bar"], tz="UTC")).days
    if age > STALE_AFTER_DAYS:
        print(f"  WARNING: newest bar is {age}d old — run scripts/update.py")


if __name__ == "__main__":
    main()
