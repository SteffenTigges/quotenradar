"""Optionale Telegram-Benachrichtigungen. Einrichtung: siehe README."""
from __future__ import annotations

import logging

import requests

from . import config

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID)


def send(text: str) -> bool:
    if not enabled():
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True},
            timeout=20,
        )
        return r.ok
    except requests.RequestException as e:
        log.warning("Telegram-Fehler: %s", e)
        return False
