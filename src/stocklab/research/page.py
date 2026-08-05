"""Renders the research briefing to one self-contained HTML file.

Same contract as `report/dashboard.py`: no server, no external requests, the
whole payload embedded as JSON. Openable from disk, and safe to hand to someone
else because there is nothing in it that phones home.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pandas as pd

from .context import Context
from .notes import Note

TEMPLATE = Path(__file__).with_name("template.html")


def build_payload(
    contexts: Mapping[str, Context],
    notes: Mapping[str, Note],
    order: list[str],
) -> dict:
    """Join the deterministic strip to the written note for each symbol."""
    today = pd.Timestamp.now(tz="UTC")
    rows = []

    for symbol in order:
        context = contexts.get(symbol)
        if context is None:
            continue

        entry: dict = {"context": context.to_dict(), "note": None}
        note = notes.get(symbol)
        if note is not None:
            entry["note"] = {
                **note.to_dict(),
                "age_days": note.age_days(today),
                "drift": note.drift(context.close),
                # Surfaced rather than hidden: a reader who cannot see that a
                # note is six weeks old will treat it as this morning's view.
                "stale_reason": note.staleness(context.close, today),
            }
        rows.append(entry)

    covered = sum(1 for row in rows if row["note"])

    return {
        "generated": today.strftime("%Y-%m-%d %H:%M UTC"),
        "last_bar": max((r["context"]["last_bar"] for r in rows), default=""),
        "covered": covered,
        "total": len(rows),
        "rows": rows,
    }


def render_briefing(payload: dict, output: str | Path) -> Path:
    """Inject the payload into the template and write the standalone page."""
    template = TEMPLATE.read_text(encoding="utf-8")

    # `</` inside <script> would close the tag early — escape it so a note
    # containing HTML cannot break the document.
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")

    path = Path(output)
    path.write_text(template.replace("__PAYLOAD__", data), encoding="utf-8")
    return path
