"""Daily refresh: pull the latest closed session, rebuild the dashboard.

    py -3 scripts/update.py [SYMBOL]

Written to be run unattended by a scheduler, so it logs one timestamped line
per run and exits non-zero when the data is stale — a silent failure that
leaves yesterday's dashboard in place looking fine is the thing to avoid.
"""

from __future__ import annotations

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
    symbol = sys.argv[1] if len(sys.argv) > 1 else "SPY"

    try:
        info = refresh_and_report(symbol, ROOT / "dashboard.html")
    except Exception:
        log(f"{symbol} FAILED\n{traceback.format_exc()}")
        return 1

    log(
        f"{info['symbol']}  {info['bars']} bars  "
        f"last {info['last_bar']} ({info['age_days']}d old)  "
        f"-> {info['path'].name}"
    )

    if info["unpriced"]:
        dates = ", ".join(str(d) for d in info["unpriced"])
        log(f"{symbol} dropped {len(info['unpriced'])} unpriced session(s): {dates}")

    if info["stale"]:
        log(f"{symbol} STALE: newest bar is {info['age_days']} days old")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
