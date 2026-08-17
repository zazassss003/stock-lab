"""Alpaca **paper** adapter — thin REST over stdlib, no extra dependencies.

Hard constraint enforced in the constructor: the base URL must be a paper
endpoint. This class cannot be pointed at live money by editing a config
string, which is the most common way a "paper only" system stops being one.

Untested against the real API in this repository — it needs credentials, which
belong to you and never to the code. `SimulatedBroker` is what the test suite
exercises; this is the same interface with HTTP behind it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .broker import Order

PAPER_HOST = "paper-api.alpaca.markets"


class AlpacaPaperBroker:
    #: Alpaca fills fractional shares on liquid US equities, so sizing may aim
    #: at a target exactly. This is the assumption the engine has always made;
    #: it is stated here because it is Alpaca's, not everyone's.
    qty_increment = 0.0

    def __init__(
        self,
        key_id: str | None = None,
        secret_key: str | None = None,
        base_url: str = f"https://{PAPER_HOST}",
        timeout: float = 15.0,
    ) -> None:
        if PAPER_HOST not in base_url:
            raise ValueError(
                f"refusing to start: {base_url!r} is not the paper endpoint. "
                "This adapter is paper-only by construction."
            )

        self.key_id = key_id or os.environ.get("ALPACA_API_KEY_ID", "")
        self.secret_key = secret_key or os.environ.get("ALPACA_API_SECRET_KEY", "")
        if not self.key_id or not self.secret_key:
            raise ValueError(
                "missing Alpaca paper credentials — set ALPACA_API_KEY_ID and "
                "ALPACA_API_SECRET_KEY in your .env (never in a tracked file)"
            )

        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            method=method,
            data=json.dumps(payload).encode() if payload else None,
            headers={
                "APCA-API-KEY-ID": self.key_id,
                "APCA-API-SECRET-KEY": self.secret_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")
            raise RuntimeError(f"Alpaca {method} {path} failed [{error.code}]: {detail}") from error

    def submit(self, order: Order) -> str:
        response = self._request(
            "POST",
            "/v2/orders",
            {
                "symbol": order.symbol,
                "qty": str(abs(order.qty)),
                "side": order.side,
                "type": "market",
                "time_in_force": "day",
            },
        )
        return response["id"]

    def positions(self) -> dict[str, float]:
        return {p["symbol"]: float(p["qty"]) for p in self._request("GET", "/v2/positions")}

    def cash(self) -> float:
        return float(self._request("GET", "/v2/account")["cash"])

    def equity(self) -> float:
        return float(self._request("GET", "/v2/account")["equity"])

    def is_ready(self) -> bool:
        """Account reachable and not restricted from trading.

        REST has no connection to lose, so this is a real request rather than a
        cached flag. `trading_blocked` catches the case the transport cannot:
        the account answers normally and refuses every order.
        """
        try:
            account = self._request("GET", "/v2/account")
        except Exception:
            return False
        return account.get("status") == "ACTIVE" and not account.get("trading_blocked", False)
