"""Minimal Binance Spot REST client (signed + public endpoints)."""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)


class BinanceAPIError(RuntimeError):
    def __init__(self, status: int, code: int | None, msg: str):
        super().__init__(f"HTTP {status} code={code}: {msg}")
        self.status = status
        self.code = code
        self.msg = msg


class BinanceClient:
    def __init__(self, base_url: str, api_key: str = "", api_secret: str = "",
                 recv_window: int = 5000, timeout: float = 10.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window = recv_window
        self.timeout = timeout
        self.session = session or requests.Session()
        self.time_offset_ms = 0

    # --- helpers ---------------------------------------------------------
    def sign(self, params: dict) -> str:
        query = urlencode(params)
        return hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()

    def _request(self, method: str, path: str, params: dict | None = None, signed: bool = False):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {}
        if signed:
            if not (self.api_key and self.api_secret):
                raise BinanceAPIError(0, None, "API key/secret required for signed endpoint")
            params["recvWindow"] = self.recv_window
            params["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
            params["signature"] = self.sign(params)
        if self.api_key:
            headers["X-MBX-APIKEY"] = self.api_key

        url = f"{self.base_url}{path}"
        resp = self.session.request(method, url, params=params, headers=headers, timeout=self.timeout)
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except ValueError:
                body = {"msg": resp.text}
            raise BinanceAPIError(resp.status_code, body.get("code"), body.get("msg", ""))
        return resp.json()

    # --- public ----------------------------------------------------------
    def ping(self) -> dict:
        return self._request("GET", "/api/v3/ping")

    def server_time(self) -> int:
        return self._request("GET", "/api/v3/time")["serverTime"]

    def sync_time(self) -> int:
        self.time_offset_ms = self.server_time() - int(time.time() * 1000)
        return self.time_offset_ms

    def exchange_info(self, symbols: list[str] | None = None) -> dict:
        params = {}
        if symbols:
            params["symbols"] = "[" + ",".join(f'"{s}"' for s in symbols) + "]"
        return self._request("GET", "/api/v3/exchangeInfo", params)

    def klines(self, symbol: str, interval: str, limit: int = 200) -> list[list]:
        return self._request("GET", "/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})

    def ticker_price(self, symbol: str) -> float:
        return float(self._request("GET", "/api/v3/ticker/price", {"symbol": symbol})["price"])

    # --- signed ----------------------------------------------------------
    def account(self) -> dict:
        return self._request("GET", "/api/v3/account", {"omitZeroBalances": "true"}, signed=True)

    def balance(self, asset: str) -> float:
        for b in self.account().get("balances", []):
            if b["asset"] == asset:
                return float(b["free"])
        return 0.0

    def new_order(self, symbol: str, side: str, order_type: str, quantity: str | None = None,
                  quote_order_qty: str | None = None, price: str | None = None,
                  time_in_force: str | None = None, client_order_id: str | None = None,
                  test: bool = False, **extra) -> dict:
        params = {
            "symbol": symbol, "side": side, "type": order_type,
            "quantity": quantity, "quoteOrderQty": quote_order_qty,
            "price": price, "timeInForce": time_in_force,
            "newClientOrderId": client_order_id, "newOrderRespType": "FULL", **extra,
        }
        path = "/api/v3/order/test" if test else "/api/v3/order"
        return self._request("POST", path, params, signed=True)

    def get_order(self, symbol: str, order_id: int | None = None, client_order_id: str | None = None) -> dict:
        return self._request("GET", "/api/v3/order",
                             {"symbol": symbol, "orderId": order_id, "origClientOrderId": client_order_id}, signed=True)

    def cancel_order(self, symbol: str, order_id: int) -> dict:
        return self._request("DELETE", "/api/v3/order", {"symbol": symbol, "orderId": order_id}, signed=True)

    def open_orders(self, symbol: str | None = None) -> list[dict]:
        return self._request("GET", "/api/v3/openOrders", {"symbol": symbol}, signed=True)
