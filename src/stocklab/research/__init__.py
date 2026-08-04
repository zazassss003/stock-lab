"""The research briefing: a reading surface, deliberately outside the trade path.

Everything in this package exists to be *read by a human*. None of it is
importable from `strategy/`, `backtest/`, or `execution/`, and it must stay that
way — see the module docstring in `notes.py` for why that boundary is load
bearing rather than stylistic.
"""

from .context import Context, build_context
from .notes import Note, load_notes, save_notes
from .page import render_briefing

__all__ = [
    "Context",
    "Note",
    "build_context",
    "load_notes",
    "render_briefing",
    "save_notes",
]
