"""Telegram alerts and remote commands (no-op when not configured)."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

import requests

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token
        self.chat_id = str(chat_id)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def call(self, method: str, http_timeout: float = 10, **params) -> dict:
        r = requests.post(API.format(token=self.token, method=method), json=params, timeout=http_timeout)
        return r.json()

    def send(self, text: str) -> bool:
        if not self.enabled:
            log.info("[telegram disabled] %s", text)
            return False
        try:
            return bool(self.call("sendMessage", chat_id=self.chat_id, text=text).get("ok"))
        except requests.RequestException as exc:
            log.warning("Telegram send failed: %s", exc)
            return False

    def get_updates(self, offset: int | None = None, timeout: int = 25) -> list[dict]:
        params = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            params["offset"] = offset
        res = self.call("getUpdates", http_timeout=timeout + 10, **params)
        return res.get("result", []) if res.get("ok") else []


class CommandBot:
    """Long-polls Telegram and answers commands from the configured chat only."""

    def __init__(self, tg: Telegram, handlers: dict[str, Callable[[], str]]):
        self.tg = tg
        self.handlers = handlers
        self.offset: int | None = None
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    def handle(self, update: dict) -> str | None:
        msg = update.get("message") or {}
        if str(msg.get("chat", {}).get("id")) != self.tg.chat_id:
            return None  # ignore strangers
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return None
        cmd = text.split()[0][1:].split("@")[0].lower()
        handler = self.handlers.get(cmd) or self.handlers.get("help")
        try:
            return handler()
        except Exception as exc:  # never let a command crash the bot
            log.exception("Telegram command /%s failed", cmd)
            return f"⚠️ /{cmd} failed: {exc}"

    async def run(self) -> None:
        if not self.tg.enabled:
            return
        while not self._stop.is_set():
            try:
                updates = await asyncio.to_thread(self.tg.get_updates, self.offset)
            except requests.RequestException as exc:
                log.warning("Telegram polling error: %s", exc)
                await asyncio.sleep(5)
                continue
            for upd in updates:
                self.offset = upd["update_id"] + 1
                reply = self.handle(upd)
                if reply:
                    await asyncio.to_thread(self.tg.send, reply)
