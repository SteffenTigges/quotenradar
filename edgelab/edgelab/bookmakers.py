"""Quotenvergleich deutscher Anbieter: Tipico, Betano, Winamax.

Quelle: The Odds API (https://the-odds-api.com) – kostenloser Schlüssel, 500 Abfragen pro Monat.
Mit dem Parameter `bookmakers` zählen bis zu 10 Anbieter wie eine Region; eine Abfrage pro Sportart und
Markt kostet daher 1 Abfrage. Standard: Sieger + Über/Unter für die großen Ligen, einmal am Tag.

Einrichtung: in `.env` eintragen
    ODDS_API_KEY=dein_schlüssel
    ODDS_API_SPORTS=soccer_germany_bundesliga,soccer_epl,basketball_nba   (optional)
"""
from __future__ import annotations

import difflib
import logging
import os
import re

import pandas as pd
import requests

from . import db
from .families import _UMLAUT

log = logging.getLogger(__name__)

API = "https://api.the-odds-api.com/v4"
BOOKS = {"tipico_de": "Tipico", "betano_uk": "Betano", "winamax_de": "Winamax"}
DEFAULT_SPORTS = [
    "soccer_germany_bundesliga", "soccer_germany_bundesliga2", "soccer_epl", "soccer_spain_la_liga",
    "soccer_italy_serie_a", "soccer_france_ligue_one", "soccer_uefa_champs_league", "soccer_uefa_europa_league",
    "basketball_nba", "americanfootball_nfl", "icehockey_nhl",
]
# Polymarket-Kürzel -> The-Odds-API-Sportart (für die Auswahl, was abgefragt wird)
CODE_TO_SPORT = {
    "bun": "soccer_germany_bundesliga", "bl2": "soccer_germany_bundesliga2", "epl": "soccer_epl", "lal": "soccer_spain_la_liga",
    "sea": "soccer_italy_serie_a", "fl1": "soccer_france_ligue_one", "ucl": "soccer_uefa_champs_league", "uel": "soccer_uefa_europa_league",
    "nba": "basketball_nba", "nfl": "americanfootball_nfl", "nhl": "icehockey_nhl",
}
REFRESH_HOURS = 24

SCHEMA = """
CREATE TABLE IF NOT EXISTS book_odds (
    event_id TEXT, sport TEXT, commence INTEGER, home TEXT, away TEXT, book TEXT, market TEXT,
    outcome TEXT, point REAL, price REAL, updated INTEGER,
    PRIMARY KEY (event_id, book, market, outcome, point)
);
"""


def api_key() -> str:
    return os.environ.get("ODDS_API_KEY", "").strip()


def enabled() -> bool:
    return bool(api_key())


def init() -> None:
    with db.conn() as con:
        con.executescript(SCHEMA)


def sports() -> list[str]:
    env = os.environ.get("ODDS_API_SPORTS", "").strip()
    return [s.strip() for s in env.split(",") if s.strip()] if env else DEFAULT_SPORTS


def refresh(force: bool = False, markets: str = "h2h,totals", progress=lambda s: None) -> int:
    """Lädt die Quoten von Tipico, Betano und Winamax. Ohne Schlüssel passiert nichts."""
    if not enabled():
        progress("Quotenvergleich: kein ODDS_API_KEY eingetragen (siehe README)")
        return 0
    init()
    now = db.now()
    last = db.kv_get("book_odds_last", 0)
    if not force and now - last < REFRESH_HOURS * 3600:
        return 0
    rows, remaining = [], None
    for sport in sports():
        try:
            r = requests.get(
                f"{API}/sports/{sport}/odds",
                params={"apiKey": api_key(), "bookmakers": ",".join(BOOKS), "markets": markets, "oddsFormat": "decimal"},
                timeout=30,
            )
        except requests.RequestException as e:
            log.warning("Odds API %s: %s", sport, e)
            continue
        remaining = r.headers.get("x-requests-remaining", remaining)
        if r.status_code == 401:
            progress("Quotenvergleich: Schlüssel ungültig")
            return 0
        if r.status_code == 429 or (remaining is not None and str(remaining).isdigit() and int(remaining) <= 0):
            progress("Quotenvergleich: Monatskontingent aufgebraucht")
            break
        if not r.ok:
            continue
        for ev in r.json():
            for bk in ev.get("bookmakers") or []:
                name = BOOKS.get(bk.get("key"))
                if not name:
                    continue
                for mk in bk.get("markets") or []:
                    for o in mk.get("outcomes") or []:
                        rows.append({
                            "event_id": ev["id"], "sport": sport, "commence": _ts(ev.get("commence_time")),
                            "home": ev.get("home_team"), "away": ev.get("away_team"), "book": name, "market": mk.get("key"),
                            "outcome": o.get("name"), "point": o.get("point") if o.get("point") is not None else -999.0,
                            "price": o.get("price"), "updated": now,
                        })
    with db.conn() as con:
        con.execute("DELETE FROM book_odds WHERE commence < ?", (now - 6 * 3600,))
        db.upsert(con, "book_odds", rows, ("event_id", "book", "market", "outcome", "point"))
    db.kv_set("book_odds_last", now)
    if remaining is not None:
        db.kv_set("book_odds_remaining", remaining)
    progress(f"Quotenvergleich: {len(rows)} Quoten geladen, noch {remaining} Abfragen diesen Monat")
    return len(rows)


def _ts(s: str | None) -> int | None:
    from .polymarket import parse_ts

    return parse_ts(s)


# ---------------------------------------------------------------- Namensabgleich

_DROP = {"fc", "sc", "cf", "ac", "afc", "sv", "vfb", "vfl", "tsg", "1", "club", "de", "cd", "ud", "ssc", "as", "rc", "the", "fk", "sk"}
_ALIAS = {"munchen": "munich", "koln": "cologne", "nurnberg": "nuremberg", "wien": "vienna", "milano": "milan", "torino": "turin",
          "lisboa": "lisbon", "praha": "prague", "sevilla": "seville", "roma": "rome"}


def _tokens(name: str) -> set[str]:
    n = (name or "").lower().translate(_UMLAUT)
    toks = {t for t in re.split(r"[^a-z0-9]+", n) if t and t not in _DROP and len(t) > 1}
    return toks | {_ALIAS[t] for t in toks if t in _ALIAS}


def name_similarity(a: str, b: str) -> float:
    """0..1 – wie gut zwei Teamnamen zusammenpassen ('FC Bayern München' ~ 'Bayern Munich')."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    best = 0.0
    for x in ta:
        for y in tb:
            if len(x) < 3 or len(y) < 3:
                continue
            best = max(best, 1.0 if x == y else difflib.SequenceMatcher(None, x, y).ratio())
    return best


SPORT_PREFIX = {"soccer": "soccer_", "basketball": "basketball_", "hockey": "icehockey_", "american-football": "americanfootball_",
                "baseball": "baseball_", "tennis": "tennis_", "darts": "darts_"}


def find_event(a: str, b: str, start_ts: int, odds: pd.DataFrame, family: str | None = None) -> str | None:
    """Sucht das passende Spiel in den Anbieter-Quoten (gleiche Sportart, Zeit ±12 h, beide Teams ähnlich)."""
    if odds.empty:
        return None
    ev = odds.drop_duplicates("event_id")
    ev = ev[(ev["commence"] - start_ts).abs() <= 12 * 3600]
    if family:
        prefix = SPORT_PREFIX.get(family)
        ev = ev[ev["sport"].str.startswith(prefix)] if prefix else ev.iloc[0:0]
    best, best_id = 0.0, None
    for r in ev.itertuples():
        s1 = min(name_similarity(a, r.home), name_similarity(b, r.away))
        s2 = min(name_similarity(a, r.away), name_similarity(b, r.home))
        s = max(s1, s2)
        if s > best:
            best, best_id = s, r.event_id
    return best_id if best >= 0.8 else None


def load() -> pd.DataFrame:
    init()
    return db.df("SELECT * FROM book_odds WHERE commence > ?", (db.now() - 6 * 3600,))


def for_game(a: str, b: str, start_ts: int, odds: pd.DataFrame | None = None, family: str | None = None) -> dict | None:
    """Quoten von Tipico/Betano/Winamax für ein Spiel:
    {'h2h': {'Tipico': {'a': 1.07, 'draw': 15.0, 'b': 21.0}, ...}, 'totals': {'2.5': {'Tipico': {'over':.., 'under':..}}}}"""
    odds = load() if odds is None else odds
    eid = find_event(a, b, start_ts, odds, family)
    if not eid:
        return None
    ev = odds[odds["event_id"] == eid]
    home = ev["home"].iloc[0]
    a_is_home = name_similarity(a, home) >= name_similarity(b, home)
    out: dict = {"h2h": {}, "totals": {}}
    for r in ev.itertuples():
        if r.market == "h2h":
            d = out["h2h"].setdefault(r.book, {})
            if r.outcome == "Draw":
                d["draw"] = r.price
            elif (r.outcome == r.home) == a_is_home:
                d["a"] = r.price
            else:
                d["b"] = r.price
        elif r.market == "totals" and r.point != -999.0:
            key = f"{r.point:g}"
            d = out["totals"].setdefault(key, {}).setdefault(r.book, {})
            d["over" if r.outcome.lower() == "over" else "under"] = r.price
    return out if out["h2h"] or out["totals"] else None


def best_value(fair_p: float | None, offers: dict[str, float]) -> tuple[str | None, float | None]:
    """Anbieter mit der höchsten Quote und deren Value gegenüber der fairen Modellquote (Quote × p − 1)."""
    if not offers:
        return None, None
    book, q = max(offers.items(), key=lambda kv: kv[1] or 0)
    return book, (q * fair_p - 1) if fair_p else None
