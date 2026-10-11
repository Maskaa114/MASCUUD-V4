"""Risk engine: position sizing, exchange-filter rounding and trade gating."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


@dataclass
class SymbolFilters:
    step_size: Decimal = Decimal("0.00001")
    min_qty: Decimal = Decimal("0.00001")
    tick_size: Decimal = Decimal("0.01")
    min_notional: Decimal = Decimal("5")

    @classmethod
    def from_exchange_info(cls, sym: dict) -> "SymbolFilters":
        f = cls()
        for flt in sym.get("filters", []):
            t = flt["filterType"]
            if t == "LOT_SIZE":
                f.step_size, f.min_qty = Decimal(flt["stepSize"]), Decimal(flt["minQty"])
            elif t == "PRICE_FILTER":
                f.tick_size = Decimal(flt["tickSize"])
            elif t in ("NOTIONAL", "MIN_NOTIONAL"):
                f.min_notional = Decimal(flt.get("minNotional", "0"))
        return f


def round_step(value: float | Decimal, step: Decimal) -> Decimal:
    v = Decimal(str(value))
    if step == 0:
        return v
    return (v / step).to_integral_value(rounding=ROUND_DOWN) * step


def fmt(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    return s


@dataclass
class TradePlan:
    symbol: str
    quantity: Decimal
    entry: Decimal
    stop_loss: Decimal
    take_profit: Decimal
    risk_amount: float


class RiskEngine:
    def __init__(self, risk_per_trade_pct: float, max_open_positions: int, max_daily_loss_pct: float,
                 max_position_pct: float, atr_sl_mult: float, atr_tp_mult: float):
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_open_positions = max_open_positions
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_position_pct = max_position_pct
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp_mult = atr_tp_mult

    @classmethod
    def from_config(cls, cfg) -> "RiskEngine":
        return cls(cfg.risk_per_trade_pct, cfg.max_open_positions, cfg.max_daily_loss_pct,
                   cfg.max_position_pct, cfg.atr_sl_mult, cfg.atr_tp_mult)

    def can_open(self, open_positions: int, daily_pnl: float, equity: float, symbol_open: bool) -> tuple[bool, str]:
        if symbol_open:
            return False, "position already open for symbol"
        if open_positions >= self.max_open_positions:
            return False, "max open positions reached"
        if equity <= 0:
            return False, "no equity"
        if daily_pnl <= -equity * self.max_daily_loss_pct / 100:
            return False, "daily loss limit hit"
        return True, "ok"

    def plan(self, symbol: str, price: float, atr_value: float, equity: float,
             filters: SymbolFilters) -> TradePlan | None:
        if not atr_value or atr_value <= 0 or price <= 0:
            return None
        stop_dist = atr_value * self.atr_sl_mult
        risk_amount = equity * self.risk_per_trade_pct / 100
        qty = risk_amount / stop_dist
        qty = min(qty, equity * self.max_position_pct / 100 / price)  # cap notional
        q = round_step(qty, filters.step_size)
        if q < filters.min_qty or q * Decimal(str(price)) < filters.min_notional:
            return None
        return TradePlan(
            symbol=symbol,
            quantity=q,
            entry=round_step(price, filters.tick_size),
            stop_loss=round_step(price - stop_dist, filters.tick_size),
            take_profit=round_step(price + atr_value * self.atr_tp_mult, filters.tick_size),
            risk_amount=float(q) * stop_dist,
        )
