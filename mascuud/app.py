"""MASCUUD-V4 command-line entry point.

    python -m mascuud check     # connectivity + credentials check
    python -m mascuud scan      # one-shot REST scan, prints signals
    python -m mascuud run       # live WebSocket loop (Testnet by default)
    python -m mascuud report    # 24H / 7D / 30D P/L report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
import time

import requests

from .binance_client import BinanceAPIError, BinanceClient
from .config import ConfigError, load_config
from .db import Database
from .market import Candle, CandleStore
from .risk import RiskEngine
from .strategy import generate_signal
from .telegram import Telegram
from .trader import Trader
from .ws_market import MarketDataEngine

log = logging.getLogger("mascuud")


def build(cfg):
    client = BinanceClient(cfg.rest_base, cfg.api_key, cfg.api_secret)
    db = Database(cfg.db_path)
    trader = Trader(cfg, client, db, RiskEngine.from_config(cfg), Telegram(cfg.telegram_token, cfg.telegram_chat_id))
    return client, db, trader


def cmd_check(cfg) -> int:
    client = BinanceClient(cfg.rest_base, cfg.api_key, cfg.api_secret)
    print(f"Mode: {cfg.mode}  dry_run={cfg.dry_run}  REST={cfg.rest_base}")
    client.ping()
    print(f"Ping OK, clock offset {client.sync_time()} ms")
    if cfg.has_credentials:
        acct = client.account()
        print(f"Account OK, canTrade={acct.get('canTrade')}")
        for b in acct.get("balances", [])[:10]:
            print(f"  {b['asset']}: free={b['free']} locked={b['locked']}")
    else:
        print("No API credentials set (BINANCE_API_KEY / BINANCE_API_SECRET) - public endpoints only.")
    return 0


def cmd_scan(cfg) -> int:
    client = BinanceClient(cfg.rest_base)
    for sym in cfg.symbols:
        candles = [Candle.from_rest(k) for k in client.klines(sym, cfg.interval, 300)][:-1]
        s = generate_signal(sym, candles)
        rsi_txt = f"{s.rsi:.1f}" if s.rsi is not None else "-"
        print(f"{sym:<10} {s.action:<4} price={s.price:<12g} regime={s.regime.value:<10} rsi={rsi_txt:<5} {s.reason}")
    return 0


def cmd_report(cfg) -> int:
    db = Database(cfg.db_path)
    now = int(time.time())
    out = {label: db.report(now - secs) for label, secs in (("24H", 86400), ("7D", 7 * 86400), ("30D", 30 * 86400))}
    out["open_positions"] = len(db.open_trades())
    print(json.dumps(out, indent=2))
    return 0


async def run_loop(cfg) -> None:
    client, db, trader = build(cfg)
    client.sync_time()
    trader.load_filters()
    store = CandleStore()
    for sym in cfg.symbols:
        store.load(sym, [Candle.from_rest(k) for k in client.klines(sym, cfg.interval, 300)][:-1])

    def on_close(symbol: str, candle: Candle) -> None:
        trader.on_candle(symbol, candle)
        sig = generate_signal(symbol, store.closed(symbol))
        log.info("%s %s regime=%s %s", symbol, sig.action, sig.regime.value, sig.reason)
        trader.on_signal(sig)

    engine = MarketDataEngine(cfg.stream_base, cfg.symbols, cfg.interval, store, on_close)
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, engine.stop)
        except NotImplementedError:  # Windows
            pass
    trader.tg.send(f"🚀 MASCUUD-V4 started [{trader.mode_label}] {','.join(cfg.symbols)} {cfg.interval}")
    try:
        await engine.run()
    finally:
        trader.tg.send("🛑 MASCUUD-V4 stopped")
        db.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mascuud", description="MASCUUD-V4 Binance Spot bot")
    p.add_argument("command", choices=["check", "scan", "run", "report"])
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    try:
        if args.command == "check":
            return cmd_check(cfg)
        if args.command == "scan":
            return cmd_scan(cfg)
        if args.command == "report":
            return cmd_report(cfg)
        asyncio.run(run_loop(cfg))
        return 0
    except BinanceAPIError as exc:
        print(f"Binance error: {exc}", file=sys.stderr)
        return 1
    except requests.RequestException as exc:
        print(f"Network error reaching {cfg.rest_base}: {exc}", file=sys.stderr)
        return 1
