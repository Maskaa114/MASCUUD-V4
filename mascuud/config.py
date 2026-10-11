"""Configuration loading and the live-trading safety gate."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

TESTNET_REST = "https://testnet.binance.vision"
TESTNET_WS_API = "wss://ws-api.testnet.binance.vision/ws-api/v3"
TESTNET_STREAM = "wss://stream.testnet.binance.vision/ws"

LIVE_REST = "https://api.binance.com"
LIVE_WS_API = "wss://ws-api.binance.com:443/ws-api/v3"
LIVE_STREAM = "wss://stream.binance.com:9443/ws"

LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THE_RISK"


class ConfigError(RuntimeError):
    pass


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _float(env: dict, key: str, default: float) -> float:
    raw = env.get(key)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Config:
    mode: str = "testnet"
    dry_run: bool = True
    api_key: str = ""
    api_secret: str = ""
    symbols: list[str] = field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    interval: str = "5m"
    quote_asset: str = "USDT"
    risk_per_trade_pct: float = 0.5
    max_open_positions: int = 3
    max_daily_loss_pct: float = 2.0
    max_position_pct: float = 20.0
    atr_sl_mult: float = 1.5
    atr_tp_mult: float = 3.0
    db_path: str = "mascuud.db"
    telegram_token: str = ""
    telegram_chat_id: str = ""

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @property
    def rest_base(self) -> str:
        return LIVE_REST if self.is_live else TESTNET_REST

    @property
    def ws_api(self) -> str:
        return LIVE_WS_API if self.is_live else TESTNET_WS_API

    @property
    def stream_base(self) -> str:
        return LIVE_STREAM if self.is_live else TESTNET_STREAM

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.api_secret)


def load_config(env: dict | None = None, dotenv: bool = True) -> Config:
    if env is None:
        if dotenv and load_dotenv is not None:
            load_dotenv()
        env = dict(os.environ)

    mode = (env.get("MASCUUD_MODE") or "testnet").strip().lower()
    if mode not in {"testnet", "live"}:
        raise ConfigError(f"MASCUUD_MODE must be 'testnet' or 'live', got {mode!r}")
    if mode == "live" and env.get("LIVE_TRADING_CONFIRM", "") != LIVE_CONFIRM_PHRASE:
        raise ConfigError(
            "Live trading is blocked. Set LIVE_TRADING_CONFIRM="
            f"{LIVE_CONFIRM_PHRASE} only after a validated Testnet run."
        )

    symbols = [s.strip().upper() for s in (env.get("SYMBOLS") or "BTCUSDT,ETHUSDT").split(",") if s.strip()]

    return Config(
        mode=mode,
        dry_run=_bool(env.get("DRY_RUN"), True),
        api_key=env.get("BINANCE_API_KEY", ""),
        api_secret=env.get("BINANCE_API_SECRET", ""),
        symbols=symbols,
        interval=env.get("INTERVAL") or "5m",
        quote_asset=(env.get("QUOTE_ASSET") or "USDT").upper(),
        risk_per_trade_pct=_float(env, "RISK_PER_TRADE_PCT", 0.5),
        max_open_positions=int(_float(env, "MAX_OPEN_POSITIONS", 3)),
        max_daily_loss_pct=_float(env, "MAX_DAILY_LOSS_PCT", 2.0),
        max_position_pct=_float(env, "MAX_POSITION_PCT", 20.0),
        atr_sl_mult=_float(env, "ATR_SL_MULT", 1.5),
        atr_tp_mult=_float(env, "ATR_TP_MULT", 3.0),
        db_path=env.get("DB_PATH") or "mascuud.db",
        telegram_token=env.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=env.get("TELEGRAM_CHAT_ID", ""),
    )
