import asyncio
import json
import math
from decimal import Decimal

import pytest

from mascuud.binance_client import BinanceClient
from mascuud.config import LIVE_CONFIRM_PHRASE, TESTNET_REST, ConfigError, load_config
from mascuud.db import Database, calc_pnl
from mascuud.indicators import atr, ema, rsi
from mascuud.market import Candle, CandleStore
from mascuud.risk import RiskEngine, SymbolFilters, round_step
from mascuud.strategy import Regime, Signal, detect_regime, generate_signal
from mascuud.telegram import Telegram
from mascuud.trader import Trader, fill_summary
from mascuud.ws_market import MarketDataEngine, stream_url


# --- config / safety gate ---------------------------------------------------
def test_default_is_testnet_dry_run():
    cfg = load_config({})
    assert cfg.mode == "testnet" and cfg.dry_run
    assert cfg.rest_base == TESTNET_REST
    assert "testnet" in cfg.stream_base and "testnet" in cfg.ws_api


def test_live_blocked_without_confirmation():
    with pytest.raises(ConfigError):
        load_config({"MASCUUD_MODE": "live"})
    cfg = load_config({"MASCUUD_MODE": "live", "LIVE_TRADING_CONFIRM": LIVE_CONFIRM_PHRASE})
    assert cfg.is_live and "testnet" not in cfg.rest_base


# --- signing (example from Binance docs) ------------------------------------
def test_hmac_signature_matches_binance_docs():
    c = BinanceClient("x", api_secret="NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j")
    params = {"symbol": "LTCBTC", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": "1",
              "price": "0.1", "recvWindow": "5000", "timestamp": "1499827319559"}
    assert c.sign(params) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


# --- indicators -------------------------------------------------------------
def test_ema_constant_series():
    assert ema([5.0] * 30, 10)[-1] == pytest.approx(5.0)
    assert ema([1.0] * 5, 10)[-1] is None


def test_rsi_extremes():
    up = [float(i) for i in range(50)]
    assert rsi(up)[-1] == 100.0
    assert rsi(list(reversed(up)))[-1] == pytest.approx(0.0)


def test_atr_constant_range():
    n = 40
    assert atr([11.0] * n, [9.0] * n, [10.0] * n)[-1] == pytest.approx(2.0)


# --- strategy ----------------------------------------------------------------
def make_candles(closes):
    return [Candle(i * 60_000, c, c * 1.002, c * 0.998, c, 1.0, i * 60_000 + 59_999) for i, c in enumerate(closes)]


def test_regime_detection():
    assert detect_regime([100 + i * 0.5 for i in range(300)]) is Regime.TREND_UP
    assert detect_regime([300 - i * 0.5 for i in range(300)]) is Regime.TREND_DOWN
    assert detect_regime([100 + math.sin(i / 3) for i in range(300)]) is Regime.RANGE
    assert detect_regime([1.0] * 10) is Regime.UNKNOWN


def test_signal_needs_history():
    assert generate_signal("BTCUSDT", make_candles([1.0] * 20)).action == "HOLD"


def test_signal_never_buys_downtrend():
    s = generate_signal("BTCUSDT", make_candles([300 - i * 0.5 for i in range(300)]))
    assert s.action == "HOLD" and s.regime is Regime.TREND_DOWN


# --- risk ----------------------------------------------------------------------
def risk():
    return RiskEngine(1.0, 2, 2.0, 50.0, 1.5, 3.0)


def test_round_step():
    assert round_step(0.123456, Decimal("0.001")) == Decimal("0.123")


def test_position_sizing_risks_configured_amount():
    plan = risk().plan("BTCUSDT", 100.0, 2.0, 10_000, SymbolFilters(step_size=Decimal("0.001")))
    # risk 100 USDT / stop distance 3.0 = 33.333 units, capped by 50% notional = 50 units
    assert plan.quantity == Decimal("33.333")
    assert float(plan.stop_loss) == pytest.approx(97.0)
    assert float(plan.take_profit) == pytest.approx(106.0)
    assert plan.risk_amount == pytest.approx(100.0, rel=1e-3)


def test_position_rejected_below_min_notional():
    assert risk().plan("X", 100.0, 2.0, 10, SymbolFilters(min_notional=Decimal("50"))) is None


def test_risk_gates():
    r = risk()
    assert r.can_open(0, 0, 1000, False)[0]
    assert not r.can_open(2, 0, 1000, False)[0]
    assert not r.can_open(0, -25, 1000, False)[0]
    assert not r.can_open(0, 0, 1000, True)[0]


def test_symbol_filters_from_exchange_info():
    f = SymbolFilters.from_exchange_info({"filters": [
        {"filterType": "LOT_SIZE", "stepSize": "0.00010000", "minQty": "0.00010000"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
        {"filterType": "NOTIONAL", "minNotional": "10.00000000"},
    ]})
    assert f.step_size == Decimal("0.0001") and f.min_notional == Decimal("10")


# --- database --------------------------------------------------------------
def test_db_trade_lifecycle(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    tid = db.open_trade("BTCUSDT", "dry", 2.0, 100.0, 97.0, 106.0, fees=0.2, ts=1000)
    assert len(db.open_trades()) == 1
    pnl = db.close_trade(tid, 106.0, "TAKE_PROFIT", extra_fees=0.2, ts=2000)
    assert pnl == pytest.approx(calc_pnl(100, 106, 2, 0.4)) == pytest.approx(11.6)
    assert db.open_trades() == []
    rep = db.report(0)
    assert rep["trades"] == 1 and rep["win_rate"] == 100.0


# --- fills -------------------------------------------------------------------
def test_fill_summary_base_commission_reduces_qty():
    order = {"symbol": "BTCUSDT", "side": "BUY", "fills": [
        {"price": "100", "qty": "1", "commission": "0.001", "commissionAsset": "BTC"},
        {"price": "102", "qty": "1", "commission": "0.001", "commissionAsset": "BTC"}]}
    avg, qty, fee = fill_summary(order, 0, 0)
    assert avg == pytest.approx(101) and qty == pytest.approx(1.998) and fee == pytest.approx(0.202)


# --- trader (fake exchange) --------------------------------------------------
class FakeClient:
    def __init__(self):
        self.orders = []

    def new_order(self, symbol, side, order_type, quantity=None, test=False, **kw):
        self.orders.append((symbol, side, quantity, test))
        return {}  # /order/test returns an empty object


def test_trader_dry_run_full_cycle(tmp_path):
    cfg = load_config({"RISK_PER_TRADE_PCT": "1"})
    db = Database(str(tmp_path / "t.db"))
    client = FakeClient()
    tr = Trader(cfg, client, db, RiskEngine.from_config(cfg), Telegram(),
                {"BTCUSDT": SymbolFilters(step_size=Decimal("0.001"))})
    tid = tr.on_signal(Signal("BTCUSDT", "BUY", 100.0, 2.0, 50.0, Regime.TREND_UP, "test"))
    assert tid and client.orders[0][1:] == ("BUY", "20", True)  # capped at 20% notional
    # A second BUY on the same symbol is blocked.
    assert tr.on_signal(Signal("BTCUSDT", "BUY", 100.0, 2.0, 50.0, Regime.TREND_UP, "test")) is None
    # Candle that hits the stop closes the trade.
    closed = tr.on_candle("BTCUSDT", Candle(0, 100, 100.5, 96.0, 96.5, 1, 1))
    assert closed == [tid] and client.orders[-1][1] == "SELL"
    assert db.get(tid).exit_reason == "STOP_LOSS" and db.get(tid).pnl < 0


# --- websocket ------------------------------------------------------------------
def test_stream_url():
    url = stream_url("wss://stream.testnet.binance.vision/ws", ["BTCUSDT", "ETHUSDT"], "5m")
    assert url == "wss://stream.testnet.binance.vision/stream?streams=btcusdt@kline_5m/ethusdt@kline_5m"


def test_ws_message_triggers_callback_on_close():
    store, seen = CandleStore(), []
    eng = MarketDataEngine("wss://x/ws", ["BTCUSDT"], "1m", store, lambda s, c: seen.append((s, c.close)))

    def msg(t, close, final):
        return json.dumps({"stream": "btcusdt@kline_1m", "data": {"e": "kline", "s": "BTCUSDT", "k": {
            "t": t, "T": t + 59999, "o": "1", "h": "2", "l": "0.5", "c": str(close), "v": "3", "x": final}}})

    asyncio.run(eng.handle_message(msg(0, 1.5, False)))
    asyncio.run(eng.handle_message(msg(0, 1.6, True)))
    assert seen == [("BTCUSDT", 1.6)] and len(store.closed("BTCUSDT")) == 1
