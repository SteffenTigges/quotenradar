"""Schlanker Client für die öffentlichen Polymarket-APIs (Gamma + Data API v2). Kein API-Key nötig."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator

import requests

from . import config

log = logging.getLogger(__name__)

_local = threading.local()


def _session() -> requests.Session:
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = "edgelab/0.1 (personal research tool)"
        _local.session = s
    return s


def get(url: str, params: Any = None, retries: int = 5, timeout: int = 60) -> Any:
    """GET mit Wiederholungen bei Netzwerkfehlern, 429 und 5xx."""
    delay = 1.0
    for attempt in range(retries):
        try:
            r = _session().get(url, params=params, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"{r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            if attempt == retries - 1:
                raise
            log.debug("Retry %s %s: %s", url, params, e)
            time.sleep(delay)
            delay = min(delay * 2, 20)
    return None


# ---------------------------------------------------------------- Gamma API


def sports() -> list[dict]:
    return get(f"{config.GAMMA_API}/sports")


MAX_OFFSET = 2000  # Gamma lehnt größere Offsets ab (HTTP 422)


class WindowFull(Exception):
    """Zeitfenster enthält mehr Märkte, als Gamma per Offset liefern kann."""


def iter_markets(params: list[tuple[str, Any]], page_size: int = 100) -> Iterator[dict]:
    """Blättert durch /markets (höchstens 100 pro Seite, Offset bis 2000)."""
    offset = 0
    while True:
        if offset >= MAX_OFFSET:
            raise WindowFull()
        batch = get(f"{config.GAMMA_API}/markets", params=params + [("limit", page_size), ("offset", offset)])
        if not batch:
            return
        yield from batch
        if len(batch) < page_size:
            return
        offset += page_size


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def markets_in_window(params: list[tuple[str, Any]], start: int, end: int, min_span: int = 900) -> list[dict]:
    """Alle Märkte mit endDate in [start, end). Teilt das Fenster, wenn es zu voll ist."""
    try:
        return list(iter_markets(params + [("end_date_min", _iso(start)), ("end_date_max", _iso(end))]))
    except WindowFull:
        if end - start <= min_span:
            log.warning("Fenster %s-%s hat >%s Märkte, Rest wird übersprungen", start, end, MAX_OFFSET)
            return list(_first_pages(params + [("end_date_min", _iso(start)), ("end_date_max", _iso(end))]))
        mid = (start + end) // 2
        return markets_in_window(params, start, mid, min_span) + markets_in_window(params, mid, end, min_span)


def _first_pages(params: list[tuple[str, Any]]) -> Iterator[dict]:
    try:
        yield from iter_markets(params)
    except WindowFull:
        return


def markets_by_condition(condition_ids: Iterable[str]) -> list[dict]:
    ids = list(dict.fromkeys(condition_ids))
    out: list[dict] = []
    for i in range(0, len(ids), 20):
        chunk = ids[i : i + 20]
        out += get(f"{config.GAMMA_API}/markets", params=[("condition_ids", c) for c in chunk] + [("limit", 100)]) or []
    return out


# ---------------------------------------------------------------- Data API v2


def trades(min_usd: float, limit: int = 500, cursor: str | None = None) -> dict:
    params: dict[str, Any] = {"filter_type": "CASH", "filter_amount": min_usd, "limit": limit, "taker_only": "true"}
    if cursor:
        params = {"cursor": cursor}
    return get(f"{config.DATA_API}/trades", params=params)


def user_stats(address: str) -> dict:
    return (get(f"{config.DATA_API}/user-stats", params={"user": address}) or {}).get("data") or {}


def leaderboard_user(address: str, category: str = "sports", period: str = "all") -> dict:
    return (
        get(f"{config.DATA_API}/leaderboard", params={"user": address, "category": category, "time_period": period})
        or {}
    ).get("data") or {}


def leaderboard(category: str = "sports", period: str = "month", sort_by: str = "PNL", limit: int = 100) -> list[dict]:
    return (
        get(
            f"{config.DATA_API}/leaderboard",
            params={"category": category, "time_period": period, "sort_by": sort_by, "limit": limit},
        )
        or {}
    ).get("data") or []


def user_trades(address: str, limit: int = 200) -> list[dict]:
    return (get(f"{config.DATA_API}/trades", params={"user": address, "limit": limit}) or {}).get("data") or []


def price_as_of(token_id: str, ts: int) -> float | None:
    """Letzter Preis eines Outcome-Tokens zum Zeitpunkt ts (z. B. Anpfiff = Schlusskurs)."""
    data = (get(f"{config.DATA_API}/prices-history", params={"token_id": token_id, "as_of": ts}) or {}).get("data") or []
    return float(data[-1]["price"]) if data else None


# ---------------------------------------------------------------- Hilfsfunktionen


def parse_ts(value: str | None) -> int | None:
    """Wandelt die verschiedenen Zeitformate von Gamma in Unix-Sekunden um."""
    if not value:
        return None
    v = value.strip().replace(" ", "T")
    if v.endswith("+00"):
        v += ":00"
    v = v.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        try:
            dt = datetime.strptime(value[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def jlist(value: Any) -> list:
    if isinstance(value, list):
        return value
    if not value:
        return []
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []


def fnum(value: Any) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None
