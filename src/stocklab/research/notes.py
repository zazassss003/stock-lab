"""Research notes: language-model output, stored as data at rest.

**Why this is a JSON file and not a function call.**

Rule 2 of CLAUDE.md says no LLM in the trade path. This package does not violate
that, and the file format is how it proves it. A note is *written* by an agent
in one process, at a time a human chose, and *read* by the renderer in another.
Nothing in `strategy/` or `execution/` imports this module, so there is no code
path by which a sentence a model wrote can become an order. The briefing is
something you read before deciding; it is not something that decides.

**Why every note carries the price it was written at.**

A note is a snapshot of an argument. Left alone for six weeks while the stock
moves 20%, it stops describing reality but keeps looking authoritative. Storing
`close_at_write` lets the page show the drift and mark a note stale, so an old
opinion cannot pass itself off as a current one.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd

# Past this much price movement since it was written, a note is shown as stale.
# Not a precise threshold — it is the point where "the setup I described" and
# "the setup in front of you" have visibly parted company.
STALE_DRIFT = 0.10

# ...and past this many days, regardless of price. Quarterly reporting means a
# note older than a quarter has almost certainly been overtaken by a filing.
STALE_DAYS = 45


@dataclass
class Catalyst:
    date: str
    label: str


@dataclass
class Note:
    """One symbol's research write-up.

    The fields mirror what the equity-research skills actually produce: a claim,
    the reasoning, dated catalysts, and the things that would break it. `risks`
    is not optional — a note without a falsifier is a pitch, not research.
    """

    symbol: str
    headline: str
    thesis: str
    written: str
    # Which agent or skill produced this, e.g. "equity-research:/earnings" or
    # "claude-opus-5:web". Recorded so a note whose source turns out to be
    # unreliable can be found and purged rather than guessed at.
    source: str
    close_at_write: float
    risks: list[str] = field(default_factory=list)
    catalysts: list[Catalyst] = field(default_factory=list)
    watch: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Note":
        data = dict(raw)
        data["catalysts"] = [Catalyst(**c) for c in data.get("catalysts", [])]
        return cls(**data)

    def age_days(self, today: pd.Timestamp | None = None) -> int:
        today = today or pd.Timestamp.now(tz="UTC")
        return int((today - pd.Timestamp(self.written)).days)

    def drift(self, close_now: float) -> float:
        """Fractional price move since the note was written."""
        if not self.close_at_write:
            return 0.0
        return close_now / self.close_at_write - 1.0

    def staleness(self, close_now: float, today: pd.Timestamp | None = None) -> str | None:
        """Why this note should be distrusted, or None if it still holds."""
        age = self.age_days(today)
        drift = self.drift(close_now)
        # Rounded before comparing: 90/100 - 1 is -0.09999999999999998 in binary
        # floating point, so an exact -10% move would slip under a threshold
        # that an exact +10% move trips. Same rule, both directions.
        if round(abs(drift), 6) >= STALE_DRIFT:
            return f"price moved {drift:+.0%} since written"
        if age >= STALE_DAYS:
            return f"written {age} days ago"
        return None


def load_notes(path: str | Path) -> dict[str, Note]:
    """Read the note store, returning empty rather than raising when absent."""
    file = Path(path)
    if not file.exists():
        return {}

    raw = json.loads(file.read_text(encoding="utf-8"))
    return {symbol: Note.from_dict(note) for symbol, note in raw.get("notes", {}).items()}


def save_notes(notes: Iterable[Note] | dict[str, Note], path: str | Path) -> Path:
    """Write the note store, keyed by symbol, newest write wins."""
    if isinstance(notes, dict):
        notes = notes.values()

    payload = {
        "updated": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
        "notes": {note.symbol: note.to_dict() for note in notes},
    }

    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return file
