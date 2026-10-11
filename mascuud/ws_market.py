"""WebSocket market-data engine with automatic reconnect.

Binance closes every WebSocket connection after 24h, and may drop it any time,
so the engine reconnects with exponential backoff and proactively rotates the
connection before the 24h limit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable

import websockets

from .market import Candle, CandleStore

log = logging.getLogger(__name__)

OnClose = Callable[[str, Candle], Awaitable[None] | None]

MAX_CONNECTION_AGE = 23 * 3600  # rotate before Binance's 24h cutoff


def stream_url(stream_base: str, symbols: list[str], interval: str) -> str:
    base = stream_base.rsplit("/ws", 1)[0]
    streams = "/".join(f"{s.lower()}@kline_{interval}" for s in symbols)
    return f"{base}/stream?streams={streams}"


class MarketDataEngine:
    def __init__(self, stream_base: str, symbols: list[str], interval: str, store: CandleStore,
                 on_candle_close: OnClose | None = None, max_backoff: float = 60.0):
        self.url = stream_url(stream_base, symbols, interval)
        self.store = store
        self.on_candle_close = on_candle_close
        self.max_backoff = max_backoff
        self._stop = asyncio.Event()
        self.reconnects = 0

    def stop(self) -> None:
        self._stop.set()

    async def handle_message(self, raw: str) -> None:
        msg = json.loads(raw)
        data = msg.get("data", msg)
        if data.get("e") != "kline":
            return
        symbol = data["s"]
        candle = Candle.from_ws(data["k"])
        if self.store.update(symbol, candle) and self.on_candle_close:
            res = self.on_candle_close(symbol, candle)
            if asyncio.iscoroutine(res):
                await res

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                log.info("Connecting %s", self.url)
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
                    backoff = 1.0
                    started = time.monotonic()
                    while not self._stop.is_set():
                        if time.monotonic() - started > MAX_CONNECTION_AGE:
                            log.info("Rotating WebSocket before 24h limit")
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            continue
                        await self.handle_message(raw)
            except (OSError, websockets.WebSocketException) as exc:
                self.reconnects += 1
                log.warning("WebSocket error: %s; reconnecting in %.0fs", exc, backoff)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(backoff * 2, self.max_backoff)
