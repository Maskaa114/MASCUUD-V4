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
- Telegram: BUY/SELL alerts with profit/loss, a daily report at 08:00 EAT, and the commands `/status`, `/report`, `/trades`, `/pause`, `/resume`
- One-command server install (`deploy/install.sh`) that runs the bot 24/7 under systemd
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
python -m mascuud telegram # connect Telegram bot (token from @BotFather)
python -m mascuud check    # ping, clock offset, account balances
python -m mascuud scan     # one-shot signal scan over SYMBOLS
python -m mascuud run      # WebSocket trading loop
python -m mascuud report   # 24H / 7D / 30D P/L
pytest                     # unit tests (offline)
```

### Run 24/7 on a server (Oracle Cloud Free / any Ubuntu VPS)
Use a server **outside the USA** (Binance blocks US IPs with HTTP 451), e.g. Oracle Cloud Always Free in Frankfurt/Amsterdam.
```bash
git clone -b claude/vibrant-babbage-cqy4mi https://github.com/Maskaa114/MASCUUD-V4.git
bash MASCUUD-V4/deploy/install.sh     # asks for Binance Testnet keys + Telegram token
```
| Task | Command |
|---|---|
| Live logs | `sudo journalctl -u mascuud -f` |
| Update to latest code | `bash ~/MASCUUD-V4/deploy/update.sh` |
| Stop / start | `sudo systemctl stop mascuud` / `sudo systemctl start mascuud` |

### Railway (GitHub deploy, no server to manage)
`railway.json` starts `python -m mascuud run` and restarts it automatically. In Railway set the
service **region to EU West** (US regions get HTTP 451 from Binance) and add the variables
`BINANCE_API_KEY`, `BINANCE_API_SECRET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
The SQLite trade history is lost on redeploy unless you attach a volume and set `DB_PATH` to it.

## Modes
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
  telegram.py        alerts + command bot
  reports.py         report / status / trades text
  envfile.py         .env updates
deploy/
  install.sh         server installer (systemd)
  update.sh          pull + restart
  app.py             CLI
tests/test_core.py
```

## Known limitations / next steps
- SL/TP are managed in software on candle closes (not exchange-side OCO orders). A gap or downtime can exit worse than the stop.
- Reconciliation against `openOrders`/balances at startup isn't implemented yet.
- The strategy is unvalidated. Run it on Testnet and backtest it before you trust it.
- Real-money trading is risky. This is not financial advice.
