"""NBA-Spielerwetten (Punkte, Rebounds, Assists, Dreier, P+R+A) wie bei Buchmachern.

Daten: ESPN-Kader und Spiellogs (kostenlos, ohne Key). Modell: gewichteter Schnitt der letzten
Spiele (neuere zählen mehr) und eine Negativ-Binomial-Verteilung mit der beobachteten Streuung.
Daraus: Wahrscheinlichkeit für „Über X,5“ – als Standardlinie der Saisonschnitt mit ,5
(z. B. 24,3 Punkte -> Über 24,5), dazu jede Linie, die Polymarket anbietet (mit Preis und EV).
"""
from __future__ import annotations

import logging
import math
import re
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import betting, config, db, injuries
from . import polymarket as pm

log = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
ESPN_ATH = "https://site.api.espn.com/apis/common/v3/sports/basketball/nba/athletes"
STATS = {"pts": "Punkte", "reb": "Rebounds", "ast": "Assists", "fg3": "Dreier", "pra": "Pkt+Reb+Ast"}
PM_TYPES = {"points": "pts", "rebounds": "reb", "assists": "ast", "threes": "fg3"}
MIN_GAMES = 5
MIN_MINUTES = 15.0
LOG_CACHE_HOURS = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS nba_players (
    player_id TEXT PRIMARY KEY, name TEXT, team TEXT, team_id TEXT, pos TEXT, updated INTEGER
);
CREATE TABLE IF NOT EXISTS player_logs (
    player_id TEXT, event_id TEXT, date_ts INTEGER, min REAL, pts REAL, reb REAL, ast REAL, fg3 REAL,
    PRIMARY KEY (player_id, event_id)
);
"""


def init() -> None:
    with db.conn() as con:
        con.executescript(SCHEMA)


# ---------------------------------------------------------------- Verteilung


def nb_over(mean: float, var: float, line: float) -> float:
    """P(X > line) für eine Negativ-Binomial-Verteilung (Poisson, wenn var <= mean)."""
    k_max = int(math.floor(line))
    if mean <= 0:
        return 0.0
    if var <= mean * 1.02:
        cdf = sum(math.exp(-mean + k * math.log(mean) - math.lgamma(k + 1)) for k in range(k_max + 1))
        return max(0.0, min(1.0, 1 - cdf))
    r = mean * mean / (var - mean)
    p = r / (r + mean)
    cdf = 0.0
    for k in range(k_max + 1):
        cdf += math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1) + r * math.log(p) + k * math.log(1 - p))
    return max(0.0, min(1.0, 1 - cdf))


def avg_line(avg: float) -> float:
    """Saisonschnitt -> Buchmacher-Linie mit ,5 (24,3 -> 24,5; 24,8 -> 24,5)."""
    return math.floor(avg) + 0.5


def player_dist(logs: pd.DataFrame, stat: str, half_life: float = 10.0) -> tuple[float, float, int]:
    """Gewichteter Mittelwert und Varianz der letzten 20 Spiele."""
    g = logs[logs["min"] > 0].sort_values("date_ts").tail(20)
    if len(g) < MIN_GAMES:
        return float("nan"), float("nan"), len(g)
    x = (g["pts"] + g["reb"] + g["ast"]).to_numpy(float) if stat == "pra" else g[stat].to_numpy(float)
    w = 0.5 ** (np.arange(len(x))[::-1] / half_life)
    mean = float(np.sum(w * x) / np.sum(w))
    var = float(np.sum(w * (x - mean) ** 2) / np.sum(w))
    return mean, max(var, mean * 1.05, 0.5), len(g)


# ---------------------------------------------------------------- Daten laden


def _parse_gamelog(pid: str, d: dict) -> list[dict]:
    names = d.get("names") or []
    idx = {n: i for i, n in enumerate(names)}
    events = d.get("events") or {}
    rows = []
    for st in d.get("seasonTypes") or []:
        if "preseason" in (st.get("displayName") or "").lower():
            continue
        for cat in st.get("categories") or []:
            for ev in cat.get("events") or []:
                s = ev.get("stats") or []
                if len(s) != len(names):
                    continue

                def num(key: str) -> float:
                    v = s[idx[key]] if key in idx else "0"
                    v = str(v).split("-")[0]
                    try:
                        return float(v)
                    except ValueError:
                        return 0.0

                info = events.get(ev.get("eventId"), {}) if isinstance(events, dict) else {}
                rows.append({
                    "player_id": pid, "event_id": ev.get("eventId"), "date_ts": pm.parse_ts(info.get("gameDate")),
                    "min": num("minutes"), "pts": num("points"), "reb": num("totalRebounds"), "ast": num("assists"),
                    "fg3": num("threePointFieldGoalsMade-threePointFieldGoalsAttempted"),
                })
    return rows


def refresh(team_names: list[str] | None = None, progress=lambda s: None) -> int:
    """Lädt Kader und Spiellogs der NBA-Teams, die in den nächsten Tagen spielen."""
    init()
    now = db.now()
    teams = pm.get(f"{ESPN}/teams")["sports"][0]["leagues"][0]["teams"]
    teams = [t["team"] for t in teams]
    if team_names:
        full = [t["displayName"] for t in teams]
        wanted = {injuries.match_team(n, full) for n in team_names} - {None}
        teams = [t for t in teams if t["displayName"] in wanted]
    players = []
    for t in teams:
        try:
            ro = pm.get(f"{ESPN}/teams/{t['id']}/roster", retries=3)
        except Exception as e:  # noqa: BLE001
            log.warning("Kader %s: %s", t["displayName"], e)
            continue
        for a in ro.get("athletes") or []:
            players.append({"player_id": str(a["id"]), "name": a.get("displayName"), "team": t["displayName"],
                            "team_id": str(t["id"]), "pos": (a.get("position") or {}).get("abbreviation"), "updated": now})
    with db.conn() as con:
        db.upsert(con, "nba_players", players, "player_id")
        fresh = {r[0] for r in con.execute("SELECT key FROM kv WHERE key LIKE 'plog:%' AND CAST(value AS INTEGER) > ?",
                                           (now - LOG_CACHE_HOURS * 3600,))}
    todo = [p["player_id"] for p in players if f"plog:{p['player_id']}" not in fresh]

    def work(pid: str) -> list[dict]:
        try:
            return _parse_gamelog(pid, pm.get(f"{ESPN_ATH}/{pid}/gamelog", retries=2) or {})
        except Exception:  # noqa: BLE001
            return []

    n = 0
    with ThreadPoolExecutor(max_workers=config.HTTP_WORKERS) as ex:
        for pid, rows in zip(todo, ex.map(work, todo)):
            with db.conn() as con:
                db.upsert(con, "player_logs", [r for r in rows if r["event_id"]], ("player_id", "event_id"))
                con.execute("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (f"plog:{pid}", str(now)))
            n += 1
    progress(f"NBA-Spieler: {len(players)} im Kader, {n} Spiellogs aktualisiert")
    db.kv_set("last_props", now)
    return len(players)


# ---------------------------------------------------------------- Polymarket-Props


def polymarket_props(hours: int = 72) -> pd.DataFrame:
    """Offene NBA-Spielerwetten auf Polymarket (Punkte, Rebounds, Assists, Dreier)."""
    now = db.now()
    params = [("closed", "false"), ("tag_id", 745)] + [("sports_market_types", t) for t in PM_TYPES]
    raw = pm.markets_in_window(params, now - 3600, now + hours * 3600 + 9 * 86400)
    rows = []
    for m in raw:
        gs = pm.parse_ts(m.get("gameStartTime"))
        if not gs or gs < now or gs > now + hours * 3600:
            continue
        q = m.get("question") or ""
        mm = re.match(r"^(.+?):\s*\w+\s*O/U\s*([\d.]+)", q)
        prices = pm.jlist(m.get("outcomePrices"))
        if not mm or len(prices) != 2:
            continue
        ask = pm.fnum(m.get("bestAsk"))
        bid = pm.fnum(m.get("bestBid"))
        rows.append({
            "player": mm.group(1).strip(), "stat": PM_TYPES.get(m.get("sportsMarketType")), "line": float(mm.group(2)),
            "price_over": ask if ask and 0 < ask < 1 else pm.fnum(prices[0]),
            "price_under": (1 - bid) if bid and 0 < bid < 1 else pm.fnum(prices[1]),
            "liquidity": pm.fnum(m.get("liquidityNum")), "event_title": (m.get("events") or [{}])[0].get("title"),
            "slug": (m.get("events") or [{}])[0].get("slug"), "game_start": gs,
        })
    return pd.DataFrame(rows)


def _pname(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower().replace("jr", "").replace("iii", "").replace("ii", ""))


def game_props(team_a: str, team_b: str, pm_props: pd.DataFrame | None = None, inj: pd.DataFrame | None = None) -> list[dict]:
    """Spielerwetten für ein Spiel. Gibt pro Spieler die Linien mit Modell-Wahrscheinlichkeit zurück."""
    init()
    players = db.df("SELECT * FROM nba_players")
    if players.empty:
        return []
    full = players["team"].unique().tolist()
    ta, tb = injuries.match_team(team_a, full), injuries.match_team(team_b, full)
    sel = players[players["team"].isin([ta, tb])]
    if sel.empty:
        return []
    logs = db.df(
        f"SELECT * FROM player_logs WHERE player_id IN ({','.join('?' * len(sel))})", tuple(sel["player_id"])
    )
    inj = inj if inj is not None else injuries.load()
    inj_status = {(_pname(r.player)): r.status for r in inj.itertuples()} if not inj.empty else {}
    out = []
    for p in sel.itertuples():
        lg = logs[logs["player_id"] == p.player_id]
        played = lg[lg["min"] > 0]
        if len(played) < MIN_GAMES or played.sort_values("date_ts").tail(10)["min"].mean() < MIN_MINUTES:
            continue
        status = inj_status.get(_pname(p.name))
        if status and status.lower() in ("out", "injured reserve", "suspension"):
            continue
        season = played
        row = {"player": p.name, "team": p.team, "side": "a" if p.team == ta else "b", "pos": p.pos, "status": status,
               "games": int(len(played)), "min": round(float(played.sort_values("date_ts").tail(10)["min"].mean()), 1), "lines": []}
        for stat, label in STATS.items():
            mean, var, n = player_dist(lg, stat)
            if math.isnan(mean):
                continue
            avg = float((season["pts"] + season["reb"] + season["ast"]).mean() if stat == "pra" else season[stat].mean())
            if stat == "fg3" and avg < 0.8:
                continue
            lines = {avg_line(avg): "Ø-Linie"}
            if pm_props is not None and not pm_props.empty:
                mine = pm_props[(pm_props["stat"] == stat) & (pm_props["player"].map(_pname) == _pname(p.name))]
                for r in mine.itertuples():
                    lines.setdefault(r.line, "Polymarket")
            for line, src in sorted(lines.items()):
                po = nb_over(mean, var, line)
                item = {"stat": stat, "label": label, "line": line, "avg": round(avg, 1), "exp": round(mean, 1),
                        "p_over": round(po, 4), "src": src}
                if pm_props is not None and not pm_props.empty:
                    hit = pm_props[(pm_props["stat"] == stat) & (pm_props["line"] == line) & (pm_props["player"].map(_pname) == _pname(p.name))]
                    if not hit.empty:
                        h = hit.iloc[0]
                        item["pm_over"] = h["price_over"]
                        item["pm_under"] = h["price_under"]
                        item["ev_over"] = round(betting.ev(po, h["price_over"], config.DEFAULT_FEE_RATE), 4) if h["price_over"] else None
                        item["ev_under"] = round(betting.ev(1 - po, h["price_under"], config.DEFAULT_FEE_RATE), 4) if h["price_under"] else None
                        item["liq"] = h["liquidity"]
                row["lines"].append(item)
        if row["lines"]:
            out.append(row)
    return sorted(out, key=lambda r: -r["min"])
