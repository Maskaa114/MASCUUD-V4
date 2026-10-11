"""Telegram alerts (no-op when not configured)."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)


class Telegram:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token
        self.chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.enabled:
            log.info("[telegram disabled] %s", text)
            return False
        try:
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                              json={"chat_id": self.chat_id, "text": text}, timeout=10)
            return r.ok
        except requests.RequestException as exc:
            log.warning("Telegram send failed: %s", exc)
            return False
