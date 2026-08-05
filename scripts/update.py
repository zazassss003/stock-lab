"""Daily refresh: pull the latest closed session, rebuild the dashboard.

    py -3 scripts/update.py [SYMBOL]

Written to be run unattended by a scheduler, so it logs one timestamped line
per run and exits non-zero when the data is stale — a silent failure that
leaves yesterday's dashboard in place looking fine is the thing to avoid.
"""

from __future__ import annotations

import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stocklab.pipeline import refresh_and_report  # noqa: E402

LOG = ROOT / "update.log"


def log(message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}"
    print(line)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def main() -> int:
    symbols = [a for a in sys.argv[1:] if not a.startswith("-")] or None

    try:
        info = refresh_and_report(symbols, ROOT / "dashboard.html")
    except Exception:
        log(f"FAILED\n{traceback.format_exc()}")
        return 1

    log(
        f"{len(info['symbols'])} symbols  newest bar {info['age_days']}d old  "
        f"-> {info['path'].name}"
    )

    for symbol, dates in info["unpriced"].items():
        listed = ", ".join(str(d) for d in dates)
        log(f"  {symbol} dropped {len(dates)} unpriced session(s): {listed}")

    write_daily_brief()

    if info["failures"]:
        for symbol, error in info["failures"].items():
            log(f"  {symbol} FETCH FAILED: {error}")
        return 1

    if info["stale"]:
        log(f"  STALE: {info['stale_symbols']} — newest bar {info['age_days']} days old")
        return 2
    return 0


def write_daily_brief() -> None:
    """Generate the daily brief after the refresh.

    Run as a subprocess so a fault in the report can never take down the data
    update — the dashboard is the thing that must survive, the brief is a
    convenience on top of it.
    """
    try:
        finished = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "daily_brief.py")],
            capture_output=True,
            text=True,
            # Explicit, because `text=True` alone decodes with the system ANSI
            # codepage — cp950 on this machine, which cannot represent the
            # child's UTF-8 output and raises mid-read.
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
        if finished.returncode == 0:
            log("  daily brief -> daily_report.md")
        else:
            log(f"  daily brief FAILED (exit {finished.returncode}): {finished.stderr.strip()[:200]}")
    except Exception as error:
        log(f"  daily brief FAILED: {error}")


if __name__ == "__main__":
    raise SystemExit(main())
