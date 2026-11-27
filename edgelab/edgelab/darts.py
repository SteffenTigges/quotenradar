"""Darts: Spielerstatistiken von Darts Orakel und ein Legs-Modell wie bei deutschen Buchmachern.

Spielstärke = 3-Dart-Average der letzten 12 Monate (dartsorakel.com, öffentliche Spielerprofile).
Daraus die Wahrscheinlichkeit, ein Leg zu gewinnen, und über den Spielmodus ("first to k Legs")
alle typischen Wetten: Sieger, genaues Ergebnis, Handicap Legs, Gesamtzahl Legs.
"""
from __future__ import annotations

import datetime
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
    "players championship": 6,          # Floor-Turniere: Best of 11 Legs
}

# Satz-Modus der WM (Sätze zu je "first to 3 Legs"): nötige Sätze je nach Runde, die Runde ergibt sich aus dem Datum.
# R1/R2 bis kurz vor Weihnachten Best of 5, R3/R4 Best of 7, Viertelfinale Best of 9, Halbfinale 11, Finale 13.
WORLD_CHAMPIONSHIP = "world championship"
WC_SETS_BY_DAY = [(27, 4), (30, 5), (33, 6), (34, 7)]  # Tag ab 1. Dezember: 27. Dez, 30. Dez, 2. Jan, 3. Jan
LEGS_PER_SET = 3

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
    rows, failed = [], 0
    for name in dict.fromkeys(n for n in names if n):
        if name in fresh and not force:
            continue
        s = slug(name)
        stats = None
        try:
            r = requests.get(PROFILE_URL.format(slug=s), timeout=20, headers={"User-Agent": "edgelab/0.1 (personal)"})
            if r.ok:
                stats = parse_profile(r.text)
            elif r.status_code != 404:
                failed += 1
                log.warning("Darts Orakel %s: HTTP %s", name, r.status_code)
                if r.status_code == 403 and failed >= 3 and not any(x["found"] for x in rows):
                    log.warning("Darts Orakel lehnt automatische Abrufe ab – für heute keine weiteren Versuche")
                    break
        except requests.RequestException as e:
            failed += 1
            log.warning("Darts Orakel %s: %s", name, e)
        rows.append({"name": name, "slug": s, "found": 1 if stats else 0, "updated": now, **{k: (stats or {}).get(k) for k in STAT_NAMES.values()}})
    with db.conn() as con:
        db.upsert(con, "darts_stats", rows, "name")
    msg = f"Darts: Statistiken für {sum(r['found'] for r in rows)} von {len(rows)} Spielern geladen"
    if failed:
        msg += f" ({failed} Abrufe bei Darts Orakel gescheitert, dann rechnet das Elo-Modell)"
    progress(msg if rows else "Darts: keine anstehenden Spiele")
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


def sets_to(title: str | None, start_ts: int | None = None) -> int | None:
    """Nötige Satzgewinne bei der WM (None, wenn kein Satz-Turnier)."""
    t = (title or "").lower()
    if WORLD_CHAMPIONSHIP not in t or any(x in t for x in ("youth", "women", "junior", "seniors", "bdo", "wdf")):
        return None
    if not start_ts:
        return 3
    d = datetime.datetime.fromtimestamp(int(start_ts), datetime.timezone.utc)
    day = d.day if d.month == 12 else 31 + d.day if d.month == 1 else 0   # fortlaufend ab 1. Dezember
    k = 3
    for first_day, sets in WC_SETS_BY_DAY:
        if day >= first_day:
            k = sets
    return k


def set_prob(avg_a: float, avg_b: float) -> float:
    """Wahrscheinlichkeit, dass A einen Satz gewinnt (first to 3 Legs)."""
    p = leg_prob(avg_a, avg_b)
    return sum(v for (a, b), v in scores(p, LEGS_PER_SET).items() if a > b)


def match_prob_sets(avg_a: float, avg_b: float, s: int) -> float:
    ps = set_prob(avg_a, avg_b)
    return sum(v for (a, b), v in scores(ps, s).items() if a > b)


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


def markets_sets(a: str, b: str, avg_a: float, avg_b: float, s: int) -> list[dict]:
    """Wetten für Satz-Spiele (WM): Sieger, Satzergebnis, Gesamtzahl Sätze."""
    from .games import item

    dist = scores(set_prob(avg_a, avg_b), s)
    win_a = sum(v for (x, y), v in dist.items() if x > y)
    groups = [{"group": "Sieger", "items": [item(a, win_a), item(b, 1 - win_a)]}]
    order = sorted(dist.items(), key=lambda kv: (-kv[0][0] + kv[0][1]))  # von klarem A-Sieg bis klarem B-Sieg
    groups.append({"group": "Satzergebnis", "items": [item(f"{x}:{y}", v) for (x, y), v in order]})
    exp_total = sum((x + y) * v for (x, y), v in dist.items())
    line = math.floor(exp_total) + 0.5
    po = sum(v for (x, y), v in dist.items() if x + y > line)
    groups.append({"group": f"Gesamtzahl Sätze (erwartet {_fl(round(exp_total, 1))})",
                   "items": [item(f"Über {_fl(line)} Sätze", po), item(f"Unter {_fl(line)} Sätze", 1 - po)]})
    return groups


def implied_diff(p_match: float, k: int | None = None, sets: int | None = None) -> float:
    """Average-Unterschied, der zu einer gegebenen Siegchance passt (Umkehrung des Modells).
    So lassen sich aus der Siegchance laut Markt alle Wetten ableiten (Legs, Handicap, Sätze)."""
    p_match = min(max(p_match, 0.005), 0.995)
    f = (lambda d: match_prob_sets(d, 0.0, sets)) if sets else (lambda d: match_prob(d, 0.0, k))
    lo, hi = -80.0, 80.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if f(mid) < p_match:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2
