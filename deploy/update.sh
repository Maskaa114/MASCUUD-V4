#!/usr/bin/env bash
# Pull the latest code and restart the MASCUUD-V4 service.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart mascuud
echo "✅ Updated and restarted. Logs: sudo journalctl -u mascuud -f"
