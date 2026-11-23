"""Darts: Spielerstatistiken von Darts Orakel und ein Legs-Modell wie bei deutschen Buchmachern.

Spielstärke = 3-Dart-Average der letzten 12 Monate (dartsorakel.com, öffentliche Spielerprofile).
Daraus die Wahrscheinlichkeit, ein Leg zu gewinnen, und über den Spielmodus ("first to k Legs")
alle typischen Wetten: Sieger, genaues Ergebnis, Handicap Legs, Gesamtzahl Legs.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata

import requests

from . import db

log = logging.getLogger(__name__)

PROFILE_URL = "https://dartsorakel.com/player/profile/stats/{slug}"
CACHE_HOURS = 24
# 1 Punkt Average-Unterschied ≈ 1,25 Prozentpunkte mehr gewonnene Legs (Faustwert aus PDC-Daten)
LEG_SCALE = 20.0

# Spielmodus großer Turniere: Legs, die zum Sieg nötig sind (first to k). Ohne Eintrag: nur Siegerwette.
FORMATS = {
    "grand slam of darts": 16,          # Viertelfinale bis Finale: Best of 31 Legs
    "players championship finals": 6,   # frühe Runden Best of 11
    "european championship": 6,
    "world grand prix": None,           # Sätze – nicht als Legs modelliert
    "world matchplay": 11,
    "premier league": 6,
    "uk open": 6,
}

STAT_NAMES = {
    "Averages": "avg", "180's": "s180", "Checkout %": "checkout", "% Legs Won": "legs_won",
    "First 9 Averages": "first9", "Highest Checkout": "high_co",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS darts_stats (
    name TEXT PRIMARY KEY, slug TEXT, avg REAL, s180 REAL, checkout REAL, legs_won REAL,
    first9 REAL, high_co REAL, found INTEGER, updated INTEGER
);
"""


def init() -> None:
    with db.conn() as con:
        con.executescript(SCHEMA)


def slug(name: str) -> str:
    """'Michael van Gerwen' -> 'michael-van-gerwen', 'Karel Sedláček' -> 'karel-sedlacek', "William O'Connor" -> 'william-oconnor'"""
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    n = n.lower().replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", n).strip("-")


def parse_profile(html: str) -> dict | None:
    """Liest die Tabelle 'Player Stats - Last 12 Months' eines Darts-Orakel-Profils."""
    i = html.find("Last 12 Months")
    if i < 0:
        return None
    body = html[i: i + 20000]
    out = {}
    for label, value in re.findall(r"<td>\s*([^<]+?)\s*</td>\s*<td[^>]*>\s*([\d.,]+)%?\s*</td>", body):
        key = STAT_NAMES.get(label.strip())
        if key and key not in out:
            out[key] = float(value.replace(",", ""))
    return out if "avg" in out else None


def fetch(names: list[str], force: bool = False, progress=lambda s: None) -> int:
    """Lädt Statistiken für die genannten Spieler (einmal pro Tag)."""
    init()
    now = db.now()
    with db.conn() as con:
        fresh = {r["name"] for r in con.execute("SELECT name FROM darts_stats WHERE updated > ?", (now - CACHE_HOURS * 3600,))}
    rows = []
    for name in dict.fromkeys(n for n in names if n):
        if name in fresh and not force:
            continue
        s = slug(name)
        stats = None
        try:
            r = requests.get(PROFILE_URL.format(slug=s), timeout=20, headers={"User-Agent": "edgelab/0.1 (personal)"})
            if r.ok:
                stats = parse_profile(r.text)
        except requests.RequestException as e:
            log.info("Darts Orakel %s: %s", name, e)
        rows.append({"name": name, "slug": s, "found": 1 if stats else 0, "updated": now, **{k: (stats or {}).get(k) for k in STAT_NAMES.values()}})
    with db.conn() as con:
        db.upsert(con, "darts_stats", rows, "name")
    progress(f"Darts: Statistiken für {sum(r['found'] for r in rows)} von {len(rows)} Spielern geladen")
    return len(rows)


def stats(name: str) -> dict | None:
    init()
    d = db.df("SELECT * FROM darts_stats WHERE name = ? AND found = 1", (name,))
    return None if d.empty else d.iloc[0].to_dict()


# ---------------------------------------------------------------- Modell


def leg_prob(avg_a: float, avg_b: float) -> float:
    """Wahrscheinlichkeit, dass A ein Leg gewinnt (Anwurf wechselt, gleicht sich über das Spiel aus)."""
    return 1.0 / (1.0 + math.exp(-(avg_a - avg_b) / LEG_SCALE))


def scores(p: float, k: int) -> dict[tuple[int, int], float]:
    """Verteilung der Endstände bei 'first to k Legs': {(Legs A, Legs B): Wahrscheinlichkeit}."""
    q = 1 - p
    out = {}
    for j in range(k):
        c = math.comb(k - 1 + j, j)
        out[(k, j)] = c * p**k * q**j
        out[(j, k)] = c * q**k * p**j
    return out


def first_to(title: str | None) -> int | None:
    t = (title or "").lower()
    for name, k in FORMATS.items():
        if name in t:
            return k
    return None


def match_prob(avg_a: float, avg_b: float, k: int | None) -> float:
    p = leg_prob(avg_a, avg_b)
    if not k:
        k = 6  # unbekannter Modus: typischer Floor-Modus (Best of 11)
    return sum(v for (a, b), v in scores(p, k).items() if a > b)


def _fl(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def markets(a: str, b: str, avg_a: float, avg_b: float, k: int | None) -> list[dict]:
    """Wettangebot wie bei Tipico/Betano/Winamax (faire Quoten)."""
    from .games import item  # vermeidet Zirkelimport

    if not k:
        p = match_prob(avg_a, avg_b, None)
        return [{"group": "Sieger", "items": [item(a, p), item(b, 1 - p)]}]
    p = leg_prob(avg_a, avg_b)
    dist = scores(p, k)
    win_a = sum(v for (x, y), v in dist.items() if x > y)
    groups = [{"group": "Sieger", "items": [item(a, win_a), item(b, 1 - win_a)]}]
    top = sorted(dist.items(), key=lambda kv: -kv[1])[:6]
    groups.append({"group": "Genaues Ergebnis (Top 6)", "items": [item(f"{x}:{y}", v) for (x, y), v in top]})
    hc = []
    for line in (1.5, 3.5, 5.5):
        pa = sum(v for (x, y), v in dist.items() if x - y > line)
        pb = sum(v for (x, y), v in dist.items() if y - x > line)
        hc += [item(f"{a} −{_fl(line)}", pa), item(f"{b} +{_fl(line)}", 1 - pa), item(f"{b} −{_fl(line)}", pb), item(f"{a} +{_fl(line)}", 1 - pb)]
    groups.append({"group": "Handicap Legs", "items": hc})
    exp_total = sum((x + y) * v for (x, y), v in dist.items())
    base = math.floor(exp_total) + 0.5
    tot = []
    for line in (base - 2, base, base + 2):
        po = sum(v for (x, y), v in dist.items() if x + y > line)
        tot += [item(f"Über {_fl(line)} Legs", po), item(f"Unter {_fl(line)} Legs", 1 - po)]
    groups.append({"group": f"Gesamtzahl Legs (erwartet {_fl(round(exp_total, 1))})", "items": tot})
    return groups
