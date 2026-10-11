#!/usr/bin/env bash
# MASCUUD-V4 one-shot installer for an Ubuntu/Debian server (Oracle Cloud, Hetzner, ...).
# Installs dependencies, asks for Binance Testnet + Telegram keys, and runs the bot
# 24/7 as a systemd service that restarts automatically (also after reboot).
#
#   git clone -b claude/vibrant-babbage-cqy4mi https://github.com/Maskaa114/MASCUUD-V4.git
#   bash MASCUUD-V4/deploy/install.sh
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="$(id -un)"
SERVICE=/etc/systemd/system/mascuud.service

echo "==> 1/5 Rakibaadda Python (sudo apt)"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git >/dev/null

echo "==> 2/5 Virtualenv + dependencies"
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> 3/5 Binance Testnet keys"
if grep -qE '^BINANCE_API_KEY=[A-Za-z0-9]{64}$' .env 2>/dev/null; then
  echo "    .env already has keys - skipping (re-run: .venv/bin/python -m mascuud setup)"
else
  until .venv/bin/python -m mascuud setup </dev/tty; do echo "    Isku day mar kale..."; done
fi
.venv/bin/python -m mascuud check

echo "==> 4/5 Telegram"
if grep -qE '^TELEGRAM_CHAT_ID=-?[0-9]+$' .env 2>/dev/null; then
  echo "    Telegram already configured - skipping (re-run: .venv/bin/python -m mascuud telegram)"
else
  .venv/bin/python -m mascuud telegram </dev/tty || echo "    Telegram setup skipped - run later: .venv/bin/python -m mascuud telegram"
fi

echo "==> 5/5 systemd service (24/7, auto-restart)"
sudo tee "$SERVICE" >/dev/null <<UNIT
[Unit]
Description=MASCUUD-V4 Binance Spot bot
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/.venv/bin/python -m mascuud run
Restart=always
RestartSec=15
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now mascuud
sleep 3
sudo systemctl --no-pager --lines=5 status mascuud || true

cat <<MSG

✅ MASCUUD-V4 hadda wuxuu u shaqeynayaa 24/7.
   Telegram: /status  /report  /trades  /pause  /resume
   Logs:     sudo journalctl -u mascuud -f
   Update:   bash $APP_DIR/deploy/update.sh
   Stop:     sudo systemctl stop mascuud
MSG
