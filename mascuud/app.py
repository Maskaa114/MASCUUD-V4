"""MASCUUD-V4 command-line entry point.

    python -m mascuud setup      # enter Binance Testnet API key/secret
    python -m mascuud telegram   # connect a Telegram bot for alerts + commands
    python -m mascuud check      # connectivity + credentials check
    python -m mascuud scan       # one-shot REST scan, prints signals
    python -m mascuud run        # live WebSocket loop (Testnet by default)
    python -m mascuud report     # 24H / 7D / 30D P/L report
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import signal
import sys
import time
from pathlib import Path

import requests

from .binance_client import BinanceAPIError, BinanceClient
from .config import ConfigError, load_config
from .db import Database
from .envfile import set_env_values
from .market import Candle, CandleStore
from .reports import HELP, report_text, status_text, trades_text
from .risk import RiskEngine
from .strategy import generate_signal
from .telegram import CommandBot, Telegram
from .trader import Trader
from .ws_market import MarketDataEngine

log = logging.getLogger("mascuud")

DAILY_REPORT_UTC_HOUR = 5  # 08:00 East Africa Time


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
    tg = Telegram(cfg.telegram_token, cfg.telegram_chat_id)
    print(f"Telegram: {'ON' if tg.enabled else 'OFF (orod: python -m mascuud telegram)'}")
    return 0


def cmd_setup() -> int:
    """Ask for the Binance Testnet API keys and save them in .env."""
    if not Path(".env.example").exists() and not Path(".env").exists():
        print("Ma helin .env.example - ka orod amarka folder-ka MASCUUD-V4.", file=sys.stderr)
        return 1
    print("MASCUUD-V4 setup (Binance Spot TESTNET)")
    key = input("1) Paste API Key, kadib riix Enter: ").strip()
    secret = input("2) Paste Secret Key, kadib riix Enter: ").strip()
    print("\033[2J\033[H", end="")  # clear the screen so the secret isn't left visible
    for name, value in (("API Key", key), ("Secret Key", secret)):
        if not re.fullmatch(r"[A-Za-z0-9]{64}", value):
            print(f"{name} ma saxna: waa inuu ahaadaa 64 xaraf/lambar, meel bannaan la'aan "
                  f"(waxaa la helay {len(value)}). Mar kale orod: python -m mascuud setup", file=sys.stderr)
            return 1
    set_env_values({"BINANCE_API_KEY": key, "BINANCE_API_SECRET": secret})
    print(f".env waa la keydiyay (key ...{key[-4:]}). Hadda orod: python -m mascuud check")
    return 0


def cmd_telegram() -> int:
    """Connect a Telegram bot: validate the token, discover the chat id, send a test message."""
    print("MASCUUD-V4 Telegram setup")
    print("Telegram-ka ka fur @BotFather -> /newbot -> koobi garee token-ka.")
    token = input("1) Paste bot token, kadib riix Enter: ").strip()
    print("\033[2J\033[H", end="")
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{30,}", token):
        print("Token-ku ma saxna (tusaale: 123456789:AAH...). Mar kale isku day.", file=sys.stderr)
        return 1
    tg = Telegram(token)
    me = tg.call("getMe")
    if not me.get("ok"):
        print(f"Telegram wuu diiday token-ka: {me.get('description')}", file=sys.stderr)
        return 1
    username = me["result"]["username"]
    tg.call("deleteWebhook")
    print(f"Bot: @{username} ✅")
    input(f"2) Telegram-ka ka fur @{username}, riix START (ama u dir /start), kadib halkan riix Enter: ")
    updates = tg.get_updates(timeout=5)
    chats = [u["message"]["chat"]["id"] for u in updates if "message" in u]
    if not chats:
        print("Fariin lagama helin. U dir /start bot-ka, kadib mar kale orod: python -m mascuud telegram",
              file=sys.stderr)
        return 1
    chat_id = str(chats[-1])
    set_env_values({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id})
    Telegram(token, chat_id).send("✅ MASCUUD-V4 waa ku xiran yahay Telegram!\n\n" + HELP)
    print(f"Telegram waa la keydiyay (chat {chat_id}). Fiiri Telegram-ka - fariin tijaabo ah ayaa kuu timid.")
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
    print(report_text(db))
    print()
    print(trades_text(db))
    return 0


def seconds_until_utc_hour(hour: int, now: float | None = None) -> float:
    now = time.time() if now is None else now
    day = 86400
    target = now - now % day + hour * 3600
    if target <= now:
        target += day
    return target - now


async def daily_reports(trader: Trader, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=seconds_until_utc_hour(DAILY_REPORT_UTC_HOUR))
        except asyncio.TimeoutError:
            await asyncio.to_thread(trader.tg.send, "🌅 Daily " + report_text(trader.db))


def command_handlers(trader: Trader) -> dict:
    def prices() -> dict[str, float]:
        out = {}
        for t in trader.db.open_trades():
            try:
                out[t.symbol] = trader.client.ticker_price(t.symbol)
            except (BinanceAPIError, requests.RequestException):
                pass
        return out

    def pause() -> str:
        trader.paused = True
        return "⏸ Paused. Bot-ku ma furayo ganacsi cusub (kuwa furan SL/TP way sii socdaan). /resume si aad u sii wado."

    def resume() -> str:
        trader.paused = False
        return "▶️ Resumed. Bot-ku wuu furi karaa ganacsi cusub."

    return {
        "start": lambda: HELP,
        "help": lambda: HELP,
        "status": lambda: status_text(trader, prices()),
        "report": lambda: report_text(trader.db),
        "trades": lambda: trades_text(trader.db),
        "pause": pause,
        "resume": resume,
    }


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
    bot = CommandBot(trader.tg, command_handlers(trader))
    stop = asyncio.Event()

    def shutdown() -> None:
        engine.stop()
        bot.stop()
        stop.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, shutdown)
        except NotImplementedError:  # Windows
            pass
    trader.tg.send(f"🚀 MASCUUD-V4 started [{trader.mode_label}]\n{', '.join(cfg.symbols)} ({cfg.interval})\n/help")
    tasks = [asyncio.create_task(bot.run()), asyncio.create_task(daily_reports(trader, stop))]
    try:
        await engine.run()
    finally:
        shutdown()
        for t in tasks:
            t.cancel()
        trader.tg.send("🛑 MASCUUD-V4 stopped")
        db.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="mascuud", description="MASCUUD-V4 Binance Spot bot")
    p.add_argument("command", choices=["setup", "telegram", "check", "scan", "run", "report"])
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        if args.command == "setup":
            return cmd_setup()
        if args.command == "telegram":
            return cmd_telegram()
        cfg = load_config()
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        return 2
    except requests.RequestException as exc:
        print(f"Network error reaching Telegram: {exc}", file=sys.stderr)
        return 1
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
