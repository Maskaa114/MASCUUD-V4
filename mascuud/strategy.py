"""Market-regime detection and signal engine."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .indicators import atr, ema, rsi
from .market import Candle


class Regime(str, Enum):
    TREND_UP = "TREND_UP"
    TREND_DOWN = "TREND_DOWN"
    RANGE = "RANGE"
    UNKNOWN = "UNKNOWN"


@dataclass
class Signal:
    symbol: str
    action: str  # BUY / HOLD  (spot, long-only)
    price: float
    atr: float | None
    rsi: float | None
    regime: Regime
    reason: str


MIN_CANDLES = 210


def detect_regime(closes: list[float], fast: int = 50, slow: int = 200, flat_pct: float = 0.002) -> Regime:
    if len(closes) < slow + 5:
        return Regime.UNKNOWN
    ef, es = ema(closes, fast), ema(closes, slow)
    f, s, s_prev = ef[-1], es[-1], es[-6]
    slope = (s - s_prev) / s_prev
    gap = (f - s) / s
    # A trend needs both a meaningful EMA gap and a sloping slow EMA.
    if gap > flat_pct and slope > 0:
        return Regime.TREND_UP
    if gap < -flat_pct and slope < 0:
        return Regime.TREND_DOWN
    return Regime.RANGE


def generate_signal(symbol: str, candles: list[Candle]) -> Signal:
    closes = [c.close for c in candles]
    price = closes[-1] if closes else 0.0
    if len(candles) < MIN_CANDLES:
        return Signal(symbol, "HOLD", price, None, None, Regime.UNKNOWN, "not enough candles")

    highs = [c.high for c in candles]
    lows = [c.low for c in candles]
    regime = detect_regime(closes)
    r = rsi(closes)
    a = atr(highs, lows, closes)[-1]
    e20 = ema(closes, 20)

    if regime is Regime.TREND_UP:
        # Pullback continuation: RSI recovers from <45 and price reclaims EMA20.
        if r[-2] is not None and r[-2] < 45 <= r[-1] and closes[-1] > e20[-1]:
            return Signal(symbol, "BUY", price, a, r[-1], regime, "uptrend pullback recovery")
    elif regime is Regime.RANGE:
        if r[-2] is not None and r[-2] < 30 <= r[-1]:
            return Signal(symbol, "BUY", price, a, r[-1], regime, "range oversold bounce")
    return Signal(symbol, "HOLD", price, a, r[-1], regime, "no setup")
