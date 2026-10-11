"""Candle model and in-memory candle store shared by REST + WebSocket feeds."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass


@dataclass
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    closed: bool = True

    @classmethod
    def from_rest(cls, k: list) -> "Candle":
        return cls(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), int(k[6]), True)

    @classmethod
    def from_ws(cls, k: dict) -> "Candle":
        return cls(int(k["t"]), float(k["o"]), float(k["h"]), float(k["l"]), float(k["c"]), float(k["v"]),
                   int(k["T"]), bool(k["x"]))


class CandleStore:
    def __init__(self, maxlen: int = 500):
        self.maxlen = maxlen
        self._data: dict[str, deque[Candle]] = {}

    def load(self, symbol: str, candles: list[Candle]) -> None:
        self._data[symbol] = deque(candles, maxlen=self.maxlen)

    def update(self, symbol: str, candle: Candle) -> bool:
        """Insert or replace the latest candle. Returns True when a candle just closed."""
        dq = self._data.setdefault(symbol, deque(maxlen=self.maxlen))
        if dq and dq[-1].open_time == candle.open_time:
            dq[-1] = candle
        elif not dq or candle.open_time > dq[-1].open_time:
            dq.append(candle)
        else:
            return False
        return candle.closed

    def closed(self, symbol: str) -> list[Candle]:
        return [c for c in self._data.get(symbol, ()) if c.closed]

    def symbols(self) -> list[str]:
        return list(self._data)
