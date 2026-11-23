"""Zentrale Einstellungen. Alles lässt sich per Umgebungsvariable oder .env-Datei überschreiben."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


DATA_DIR = Path(os.environ.get("EDGELAB_DATA", ROOT / "data"))
DB_PATH = DATA_DIR / "edgelab.sqlite"

GAMMA_API = "https://gamma-api.polymarket.com"
DATA_API = "https://data-api.polymarket.com/v2"

# Wie viele Tage Ergebnisse beim ersten Start geladen werden (mehr = bessere Modelle, längerer Download).
BACKFILL_DAYS = int(_f("EDGELAB_BACKFILL_DAYS", 180))
# Wie weit in die Zukunft offene Spiele geladen werden.
LOOKAHEAD_HOURS = int(_f("EDGELAB_LOOKAHEAD_HOURS", 72))

# Whale Tracker
WHALE_MIN_USD = _f("EDGELAB_WHALE_MIN_USD", 2000)
WHALE_NOTIFY_USD = _f("EDGELAB_WHALE_NOTIFY_USD", 5000)
WALLET_CACHE_HOURS = _f("EDGELAB_WALLET_CACHE_HOURS", 12)

# Standard-Taker-Gebühr, falls ein Markt keine eigene angibt (fee = Anteile × rate × p × (1-p)).
DEFAULT_FEE_RATE = _f("EDGELAB_FEE_RATE", 0.05)

# Telegram (optional)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Sportarten, die komplett ausgeblendet werden (kommagetrennt). Standard: Esports.
EXCLUDED_FAMILIES = {x.strip() for x in os.environ.get("EDGELAB_EXCLUDE", "esports").split(",") if x.strip()}

HTTP_WORKERS = int(_f("EDGELAB_HTTP_WORKERS", 6))
