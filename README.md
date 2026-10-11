# MASCUUD-V4

Binance **Spot** trading bot foundation. It runs on **Testnet first**, and live trading is locked by default.

## Features
- Binance Spot REST client (HMAC-SHA256 signing, time sync, exchange filters)
- WebSocket kline engine with auto-reconnect, exponential backoff and rotation before Binance's 24h connection limit
- EMA / RSI / ATR indicators (pure Python)
- Market-regime detection (EMA50/EMA200 trend vs range)
- Signal engine (long-only spot: uptrend pullback, range oversold bounce)
- Risk engine: % risk per trade, ATR stop/target, max open positions, daily loss limit, notional cap, LOT_SIZE / PRICE_FILTER / NOTIONAL rounding
- SQLite trade store with P/L that accounts for fees, plus 24H / 7D / 30D reports
- Open trades are persisted, so SL/TP management resumes after a restart
- Telegram alerts (optional)
- Live-trading safety gate

## Endpoints
| | Testnet (default) | Live |
|---|---|---|
| REST | `https://testnet.binance.vision/api` | `https://api.binance.com/api` |
| WS API | `wss://ws-api.testnet.binance.vision/ws-api/v3` | `wss://ws-api.binance.com:443/ws-api/v3` |
| Streams | `wss://stream.testnet.binance.vision/ws` | `wss://stream.binance.com:9443/ws` |

## Setup
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env      # then put your *Testnet* API key/secret in .env
```
Get Testnet keys at https://testnet.binance.vision (log in with GitHub, then "Generate HMAC_SHA256 Key").
Testnet funds are virtual and Binance resets the Testnet data from time to time.

**Never commit `.env`, and never paste your API secret into a chat.**

## Usage
```bash
python -m mascuud setup    # asks for Testnet API key/secret, writes .env
python -m mascuud check    # ping, clock offset, account balances
python -m mascuud scan     # one-shot signal scan over SYMBOLS
python -m mascuud run      # WebSocket trading loop
python -m mascuud report   # 24H / 7D / 30D P/L
pytest                     # unit tests (offline)
```

### Modes
| `.env` | Behaviour |
|---|---|
| `MASCUUD_MODE=testnet`, `DRY_RUN=true` (default) | Orders are validated with `/api/v3/order/test` and never filled. Trades are simulated at signal price with 10,000 USDT paper equity. |
| `MASCUUD_MODE=testnet`, `DRY_RUN=false` | Real Testnet MARKET orders with virtual funds. Fills and fees come from the order response. |
| `MASCUUD_MODE=live` | **Refused** unless `LIVE_TRADING_CONFIRM=I_UNDERSTAND_THE_RISK`. Do this only after a long, validated Testnet run. |

## Layout
```
mascuud/
  config.py          env loading, endpoints, live gate
  binance_client.py  REST client
  ws_market.py       WebSocket engine + reconnect
  market.py          Candle + CandleStore
  indicators.py      EMA / RSI / ATR
  strategy.py        regime + signals
  risk.py            sizing, filters, gates
  trader.py          order execution, SL/TP exits
  db.py              SQLite trades + reports
  telegram.py        alerts
  app.py             CLI
tests/test_core.py
```

## Known limitations / next steps
- SL/TP are managed in software on candle closes (not exchange-side OCO orders). A gap or downtime can exit worse than the stop.
- Reconciliation against `openOrders`/balances at startup isn't implemented yet.
- The strategy is unvalidated. Run it on Testnet and backtest it before you trust it.
- Real-money trading is risky. This is not financial advice.
