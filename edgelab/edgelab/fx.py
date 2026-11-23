"""Währung und deutsche Zahlenformate.

Polymarket rechnet in US-Dollar (USDC). Für die Anzeige wird alles mit dem offiziellen Referenzkurs der
Europäischen Zentralbank in Euro umgerechnet (einmal täglich geladen, ohne Schlüssel).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime

import requests

from . import config, db

log = logging.getLogger(__name__)

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
FALLBACK_EUR_USD = 1.10  # nur falls die EZB nicht erreichbar ist
WOCHENTAGE = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]

_rate_cache: tuple[int, float] | None = None


def parse_ecb(xml: str) -> float | None:
    m = re.search(r"currency=['\"]USD['\"]\s+rate=['\"]([\d.]+)['\"]", xml)
    return float(m.group(1)) if m else None


def eur_usd() -> float:
    """Wie viele Dollar ein Euro kostet (EZB-Referenzkurs, täglich aktualisiert)."""
    global _rate_cache
    now = db.now()
    if _rate_cache and now - _rate_cache[0] < 3600:
        return _rate_cache[1]
    saved = db.kv_get("fx_eur_usd")
    if saved and now - saved.get("ts", 0) < 12 * 3600:
        _rate_cache = (now, saved["rate"])
        return saved["rate"]
    rate = None
    try:
        r = requests.get(ECB_URL, timeout=15)
        if r.ok:
            rate = parse_ecb(r.text)
    except requests.RequestException as e:
        log.info("EZB-Kurs nicht erreichbar: %s", e)
    if rate:
        db.kv_set("fx_eur_usd", {"rate": rate, "ts": now})
    else:
        rate = (saved or {}).get("rate") or float(getattr(config, "EUR_USD", 0) or FALLBACK_EUR_USD)
    _rate_cache = (now, rate)
    return rate


def to_eur(usd: float | None) -> float | None:
    if usd is None:
        return None
    try:
        return float(usd) / eur_usd()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- deutsche Formate


def zahl(x: float | None, nachkomma: int = 0) -> str:
    """1234567.891 -> '1.234.567,89'"""
    if x is None or x != x:  # None oder NaN
        return "–"
    s = f"{x:,.{nachkomma}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def euro(x: float | None, nachkomma: int = 0) -> str:
    return "–" if x is None or x != x else f"{zahl(x, nachkomma)} €"


def prozent(x: float | None, nachkomma: int = 1, vorzeichen: bool = False) -> str:
    if x is None or x != x:
        return "–"
    s = zahl(abs(x) * 100, nachkomma)
    sign = ("+" if x >= 0 else "−") if vorzeichen else ("−" if x < 0 else "")
    return f"{sign}{s} %"


def quote(x: float | None) -> str:
    return "–" if not x else zahl(x, 2)


def datum(ts: int | float | None, mit_zeit: bool = True) -> str:
    """Unix-Zeit -> 'Fr 20.11. 20:30' (deutsche Zeit)."""
    if not ts:
        return "–"
    import pandas as pd

    d = pd.Timestamp(int(ts), unit="s", tz="UTC").tz_convert("Europe/Berlin")
    s = f"{WOCHENTAGE[d.weekday()]} {d:%d.%m.}"
    return f"{s} {d:%H:%M}" if mit_zeit else s


def wochentag_zeit(d: datetime) -> str:
    return f"{WOCHENTAGE[d.weekday()]} {d:%d.%m. %H:%M}"
