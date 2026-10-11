"""Human-readable text reports for Telegram and the CLI."""

from __future__ import annotations

import time
from datetime import datetime, timezone

PERIODS = (("24H", 86400), ("7D", 7 * 86400), ("30D", 30 * 86400))


def _ts(ts: int | None) -> str:
    return datetime.fromtimestamp(ts or 0, timezone.utc).strftime("%m-%d %H:%M UTC")


def period_reports(db, now: int | None = None) -> dict:
    now = now or int(time.time())
    return {label: db.report(now - secs) for label, secs in PERIODS}


def report_text(db, now: int | None = None) -> str:
    lines = ["📊 MASCUUD-V4 REPORT"]
    for label, r in period_reports(db, now).items():
        icon = "🟢" if r["net_pnl"] > 0 else "🔴" if r["net_pnl"] < 0 else "⚪"
        pf = r["profit_factor"] if r["profit_factor"] is not None else "-"
        lines.append(
            f"\n{icon} {label}\n"
            f"  Trades: {r['trades']}  (✅ {r['wins']} win / ❌ {r['losses']} loss)\n"
            f"  Win rate: {r['win_rate']}%\n"
            f"  Profit/Loss: {r['net_pnl']:+.2f} USDT\n"
            f"  Profit factor: {pf}"
        )
    lines.append(f"\n📂 Open positions: {len(db.open_trades())}")
    return "\n".join(lines)


def trades_text(db, limit: int = 10) -> str:
    trades = db.recent_closed(limit)
    if not trades:
        return "📜 No closed trades yet."
    lines = [f"📜 Last {len(trades)} trades"]
    for t in trades:
        pct = (t.exit_price / t.entry_price - 1) * 100 if t.entry_price else 0
        icon = "✅" if (t.pnl or 0) > 0 else "❌"
        lines.append(
            f"{icon} #{t.id} {t.symbol}  BUY {t.entry_price:g} → SELL {t.exit_price:g}  "
            f"{pct:+.2f}%  P/L {t.pnl:+.2f}  ({t.exit_reason}, {_ts(t.closed_at)})"
        )
    return "\n".join(lines)


def status_text(trader, prices: dict[str, float] | None = None) -> str:
    db = trader.db
    prices = prices or {}
    state = "⏸ PAUSED (no new buys)" if trader.paused else "▶️ RUNNING"
    lines = [
        "🤖 MASCUUD-V4 STATUS",
        f"Mode: {trader.mode_label}   {state}",
        f"Symbols: {', '.join(trader.cfg.symbols)} ({trader.cfg.interval})",
        f"Today P/L: {db.realized_since(trader.day_start()):+.2f} USDT",
    ]
    open_trades = db.open_trades()
    if not open_trades:
        lines.append("📂 No open positions")
    for t in open_trades:
        px = prices.get(t.symbol)
        upnl = f"  now {px:g}  uP/L {t.unrealized(px):+.2f}" if px else ""
        lines.append(f"📂 #{t.id} {t.symbol} qty {t.quantity:g} @ {t.entry_price:g}  "
                     f"SL {t.stop_loss:g} TP {t.take_profit:g}{upnl}")
    return "\n".join(lines)


HELP = (
    "🤖 MASCUUD-V4 commands\n"
    "/status – bot state + open positions\n"
    "/report – profit/loss 24H / 7D / 30D\n"
    "/trades – last 10 BUY/SELL trades\n"
    "/pause – stop opening new trades\n"
    "/resume – allow new trades again\n"
    "/help – this list"
)
