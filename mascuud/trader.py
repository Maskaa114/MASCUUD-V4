"""Trading engine: turns signals into orders and manages SL/TP exits.

Exits are managed in software on every candle update (stop-loss on the candle
low, take-profit on the candle high). Open trades live in SQLite, so after a
restart the bot keeps managing them.
"""

from __future__ import annotations

import logging
import time
import uuid
from decimal import Decimal

from .binance_client import BinanceAPIError, BinanceClient
from .config import Config
from .db import Database
from .market import Candle
from .risk import RiskEngine, SymbolFilters, fmt, round_step
from .strategy import Signal
from .telegram import Telegram

log = logging.getLogger(__name__)


def fill_summary(order: dict, fallback_price: float, fallback_qty: float) -> tuple[float, float, float]:
    """Return (avg_price, net_qty, quote_fee) from a FULL order response.

    Commission paid in the base asset reduces the quantity actually held, so it
    is subtracted from the quantity (otherwise the exit sell would fail).
    """
    fills = order.get("fills") or []
    if not fills:
        return fallback_price, fallback_qty, 0.0
    qty = sum(float(f["qty"]) for f in fills)
    avg = sum(float(f["price"]) * float(f["qty"]) for f in fills) / qty
    symbol = order.get("symbol", "")
    fee = 0.0
    for f in fills:
        c, asset = float(f["commission"]), f.get("commissionAsset", "")
        if asset and symbol.startswith(asset) and order.get("side") == "BUY":
            qty -= c
            fee += c * float(f["price"])
        elif asset and symbol.startswith(asset):
            fee += c * float(f["price"])
        else:
            fee += c  # quote asset (or BNB, approximated)
    return avg, qty, fee


class Trader:
    def __init__(self, cfg: Config, client: BinanceClient, db: Database, risk: RiskEngine,
                 telegram: Telegram, filters: dict[str, SymbolFilters] | None = None):
        self.cfg = cfg
        self.client = client
        self.db = db
        self.risk = risk
        self.tg = telegram
        self.filters = filters or {}
        self.paper_equity = 10_000.0
        self.paused = False

    @property
    def mode_label(self) -> str:
        return "dry" if self.cfg.dry_run else self.cfg.mode

    def load_filters(self) -> None:
        info = self.client.exchange_info(self.cfg.symbols)
        for s in info.get("symbols", []):
            self.filters[s["symbol"]] = SymbolFilters.from_exchange_info(s)

    def equity(self) -> float:
        if self.cfg.dry_run or not self.cfg.has_credentials:
            return self.paper_equity + self.db.realized_since(0)
        return self.client.balance(self.cfg.quote_asset)

    @staticmethod
    def day_start() -> int:
        now = int(time.time())
        return now - now % 86400

    # --- entries ---------------------------------------------------------
    def on_signal(self, sig: Signal) -> int | None:
        if sig.action != "BUY":
            return None
        if self.paused:
            log.info("%s BUY skipped: bot is paused", sig.symbol)
            return None
        open_trades = self.db.open_trades()
        equity = self.equity()
        ok, why = self.risk.can_open(
            len(open_trades), self.db.realized_since(self.day_start()), equity,
            any(t.symbol == sig.symbol for t in open_trades),
        )
        if not ok:
            log.info("%s BUY skipped: %s", sig.symbol, why)
            return None
        filters = self.filters.get(sig.symbol, SymbolFilters())
        plan = self.risk.plan(sig.symbol, sig.price, sig.atr or 0, equity, filters)
        if plan is None:
            log.info("%s BUY skipped: size below exchange minimums", sig.symbol)
            return None

        cid = f"mv4-{uuid.uuid4().hex[:20]}"
        try:
            order = self.client.new_order(sig.symbol, "BUY", "MARKET", quantity=fmt(plan.quantity),
                                          client_order_id=cid, test=self.cfg.dry_run)
        except BinanceAPIError as exc:
            log.error("Entry order failed for %s: %s", sig.symbol, exc)
            self.tg.send(f"❌ MASCUUD-V4 entry failed {sig.symbol}: {exc.msg}")
            return None

        price, qty, fee = fill_summary(order, sig.price, float(plan.quantity))
        if not self.cfg.dry_run and order.get("status") not in ("FILLED", "PARTIALLY_FILLED"):
            log.warning("Entry order %s not filled: %s", cid, order.get("status"))
            return None
        trade_id = self.db.open_trade(sig.symbol, self.mode_label, qty, price, float(plan.stop_loss),
                                      float(plan.take_profit), fee, str(order.get("orderId", cid)))
        self.tg.send(f"🟢 BUY {sig.symbol}  [{self.mode_label}]\n"
                     f"Price: {price:g}\nQty: {qty:g}  (≈{price * qty:.2f} USDT)\n"
                     f"Stop-loss: {plan.stop_loss}\nTake-profit: {plan.take_profit}\n"
                     f"Reason: {sig.reason}")
        return trade_id

    # --- exits -----------------------------------------------------------
    def on_candle(self, symbol: str, candle: Candle) -> list[int]:
        closed = []
        for t in self.db.open_trades():
            if t.symbol != symbol:
                continue
            if candle.low <= t.stop_loss:
                reason, px = "STOP_LOSS", t.stop_loss
            elif candle.high >= t.take_profit:
                reason, px = "TAKE_PROFIT", t.take_profit
            else:
                continue
            if self.close(t.id, px, reason):
                closed.append(t.id)
        return closed

    def close(self, trade_id: int, ref_price: float, reason: str) -> bool:
        t = self.db.get(trade_id)
        filters = self.filters.get(t.symbol, SymbolFilters())
        qty = round_step(Decimal(str(t.quantity)), filters.step_size)
        try:
            order = self.client.new_order(t.symbol, "SELL", "MARKET", quantity=fmt(qty),
                                          client_order_id=f"mv4x-{uuid.uuid4().hex[:19]}",
                                          test=self.cfg.dry_run)
        except BinanceAPIError as exc:
            log.error("Exit order failed for trade %s: %s", trade_id, exc)
            self.tg.send(f"⚠️ MASCUUD-V4 exit failed {t.symbol} #{trade_id}: {exc.msg}")
            return False
        price, _, fee = fill_summary(order, ref_price, float(qty))
        pnl = self.db.close_trade(trade_id, price, reason, fee, str(order.get("orderId", "")))
        pct = (price / t.entry_price - 1) * 100 if t.entry_price else 0.0
        icon = "✅ PROFIT" if pnl > 0 else "❌ LOSS"
        self.tg.send(f"🔴 SELL {t.symbol}  [{self.mode_label}]  {icon}\n"
                     f"Reason: {reason}\nBuy: {t.entry_price:g} → Sell: {price:g} ({pct:+.2f}%)\n"
                     f"P/L: {pnl:+.2f} USDT\nToday: {self.db.realized_since(self.day_start()):+.2f} USDT")
        return True
