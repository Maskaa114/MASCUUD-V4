"""SQLite persistence for trades and P/L reporting."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL DEFAULT 'BUY',
    status TEXT NOT NULL,            -- OPEN / CLOSED
    mode TEXT NOT NULL,              -- testnet / live / dry
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    exit_price REAL,
    exit_reason TEXT,
    fees REAL NOT NULL DEFAULT 0,
    pnl REAL,
    entry_order_id TEXT,
    exit_order_id TEXT,
    opened_at INTEGER NOT NULL,
    closed_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
"""


@dataclass
class Trade:
    id: int
    symbol: str
    side: str
    status: str
    mode: str
    quantity: float
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_price: float | None
    exit_reason: str | None
    fees: float
    pnl: float | None
    entry_order_id: str | None
    exit_order_id: str | None
    opened_at: int
    closed_at: int | None

    def unrealized(self, price: float) -> float:
        return (price - self.entry_price) * self.quantity - self.fees


def calc_pnl(entry: float, exit_: float, qty: float, fees: float = 0.0) -> float:
    return (exit_ - entry) * qty - fees


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def open_trade(self, symbol: str, mode: str, quantity: float, entry: float, sl: float, tp: float,
                   fees: float = 0.0, order_id: str | None = None, ts: int | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO trades(symbol,status,mode,quantity,entry_price,stop_loss,take_profit,fees,"
            "entry_order_id,opened_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (symbol, "OPEN", mode, quantity, entry, sl, tp, fees, order_id, ts or int(time.time())),
        )
        self.conn.commit()
        return cur.lastrowid

    def close_trade(self, trade_id: int, exit_price: float, reason: str, extra_fees: float = 0.0,
                    order_id: str | None = None, ts: int | None = None) -> float:
        t = self.get(trade_id)
        fees = t.fees + extra_fees
        pnl = calc_pnl(t.entry_price, exit_price, t.quantity, fees)
        self.conn.execute(
            "UPDATE trades SET status='CLOSED', exit_price=?, exit_reason=?, fees=?, pnl=?, exit_order_id=?,"
            " closed_at=? WHERE id=?",
            (exit_price, reason, fees, pnl, order_id, ts or int(time.time()), trade_id),
        )
        self.conn.commit()
        return pnl

    def get(self, trade_id: int) -> Trade:
        row = self.conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        return Trade(**dict(row))

    def open_trades(self) -> list[Trade]:
        return [Trade(**dict(r)) for r in self.conn.execute("SELECT * FROM trades WHERE status='OPEN'")]

    def realized_since(self, since_ts: int) -> float:
        row = self.conn.execute(
            "SELECT COALESCE(SUM(pnl),0) FROM trades WHERE status='CLOSED' AND closed_at>=?", (since_ts,)
        ).fetchone()
        return float(row[0])

    def report(self, since_ts: int) -> dict:
        rows = self.conn.execute(
            "SELECT pnl FROM trades WHERE status='CLOSED' AND closed_at>=?", (since_ts,)
        ).fetchall()
        pnls = [r[0] for r in rows]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        return {
            "trades": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(100 * len(wins) / len(pnls), 2) if pnls else 0.0,
            "net_pnl": round(sum(pnls), 8),
            "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None,
        }
