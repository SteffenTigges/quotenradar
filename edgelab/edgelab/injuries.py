"""Verletzungen und Ausfälle.

Quelle: öffentliche ESPN-Verletzungslisten (ohne Key) für NBA, WNBA, NFL, NHL, MLB.
Für Basketball wird über die Saisonstatistik grob geschätzt, wie viele Punkte ein Ausfall kostet,
für American Football über die Position (Quarterback zählt am meisten).

Wichtig: Spieler, die schon länger fehlen, stecken bereits in der aktuellen Form der Teams (und damit
im Modell). Angepasst wird deshalb nur bei frischen Ausfällen (Standard: jünger als 21 Tage).
"""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pandas as pd

from . import config, db
from . import polymarket as pm

log = logging.getLogger(__name__)

ESPN = "https://site.api.espn.com/apis/site/v2/sports"
ESPN_ATHLETE = "https://site.api.espn.com/apis/common/v3/sports"

# Polymarket-Kürzel -> ESPN-Pfad
LEAGUES = {
    "nba": "basketball/nba",
    "wnba": "basketball/wnba",
    "nfl": "football/nfl",
    "cfb": "football/college-football",
    "nhl": "hockey/nhl",
    "mlb": "baseball/mlb",
}
BASKETBALL = {"nba", "wnba"}
FOOTBALL = {"nfl", "cfb"}

STATUS_WEIGHT = {
    "active": 0.0,
    "out": 1.0,
    "injured reserve": 1.0,
    "suspension": 1.0,
    "doubtful": 0.75,
    "questionable": 0.4,
    "day-to-day": 0.3,
    "probable": 0.1,
}
NFL_POSITION_PTS = {"QB": 5.5, "RB": 0.6, "WR": 0.8, "TE": 0.5, "OT": 0.4, "G": 0.3, "C": 0.4,
                    "DE": 0.4, "DT": 0.3, "LB": 0.3, "CB": 0.5, "S": 0.3, "K": 0.3, "P": 0.1}
FRESH_DAYS = 21
MAX_TEAM_ADJ = 12.0
ELO_PER_POINT = 25.0  # 1 Punkt erwartete Differenz ≈ 25 Elo (NBA/NFL-Faustregel)

SCHEMA = """
CREATE TABLE IF NOT EXISTS injuries (
    league TEXT,
    team TEXT,
    player TEXT,
    player_id TEXT,
    position TEXT,
    status TEXT,
    date_ts INTEGER,
    comment TEXT,
    return_date TEXT,
    ppg REAL, mpg REAL,
    impact REAL,          -- geschätzte Punkte, die das Team verliert (vor Status-Gewichtung)
    weight REAL,          -- Status-Gewicht × frisch?
    updated INTEGER,
    PRIMARY KEY (league, player_id)
);
CREATE TABLE IF NOT EXISTS athlete_stats (
    league TEXT, player_id TEXT, ppg REAL, rpg REAL, apg REAL, spg REAL, bpg REAL, tpg REAL, mpg REAL, gp REAL,
    updated INTEGER, PRIMARY KEY (league, player_id)
);
"""


def init() -> None:
    with db.conn() as con:
        con.executescript(SCHEMA)


STATUS_DE = {
    "out": "fällt aus", "injured reserve": "langzeitverletzt", "suspension": "gesperrt", "doubtful": "unwahrscheinlich",
    "questionable": "fraglich", "day-to-day": "fraglich", "probable": "wahrscheinlich dabei", "active": "spielt",
}


def status_de(status: str | None) -> str:
    return STATUS_DE.get((status or "").strip().lower(), status or "")


def status_weight(status: str | None) -> float:
    return STATUS_WEIGHT.get((status or "").strip().lower(), 0.5)


def basketball_impact(ppg: float, rpg: float, apg: float, spg: float, bpg: float, tpg: float, mpg: float) -> float:
    """Grobe Schätzung: Punkte Differenz, die ein Ausfall kostet (Ersatzspieler fängt einen Teil auf)."""
    prod = ppg + 0.7 * apg + 0.4 * rpg + spg + bpg - tpg
    impact = 0.15 * max(prod, 0.0)
    if mpg < 15:
        impact *= 0.5
    return round(min(impact, 7.0), 2)


def football_impact(position: str | None) -> float:
    return NFL_POSITION_PTS.get((position or "").upper(), 0.2)


def _athlete_stats(league: str, player_id: str) -> dict | None:
    path = LEAGUES[league]
    try:
        d = pm.get(f"{ESPN_ATHLETE}/{path}/athletes/{player_id}/overview", retries=2)
    except Exception:  # noqa: BLE001
        return None
    st = (d or {}).get("statistics") or {}
    names = st.get("names") or []
    for split in st.get("splits") or []:
        vals = split.get("stats") or []
        if len(vals) != len(names):
            continue
        row = dict(zip(names, vals))

        def f(k: str) -> float:
            try:
                return float(str(row.get(k, 0)).replace(",", ""))
            except ValueError:
                return 0.0

        return {"ppg": f("avgPoints"), "rpg": f("avgRebounds"), "apg": f("avgAssists"), "spg": f("avgSteals"),
                "bpg": f("avgBlocks"), "tpg": f("avgTurnovers"), "mpg": f("avgMinutes"), "gp": f("gamesPlayed")}
    return None


def refresh(progress=lambda s: None) -> int:
    init()
    now = db.now()
    rows = []
    for league, path in LEAGUES.items():
        try:
            d = pm.get(f"{ESPN}/{path}/injuries", retries=3)
        except Exception as e:  # noqa: BLE001
            log.warning("Verletzungen %s: %s", league, e)
            continue
        for team in (d or {}).get("injuries") or []:
            tname = team.get("displayName") or ""
            for inj in team.get("injuries") or []:
                ath = inj.get("athlete") or {}
                m = re.search(r"/id/(\d+)", " ".join(l.get("href", "") for l in ath.get("links") or []))
                pid = m.group(1) if m else ath.get("displayName", "")
                details = inj.get("details") or {}
                rows.append({
                    "league": league, "team": tname, "player": ath.get("displayName"), "player_id": pid,
                    "position": (ath.get("position") or {}).get("abbreviation"),
                    "status": inj.get("status"), "date_ts": pm.parse_ts(inj.get("date")),
                    "comment": inj.get("shortComment") or inj.get("longComment") or "",
                    "return_date": details.get("returnDate"), "updated": now,
                })

    # Basketball: Saisonstatistik der verletzten Spieler (einmal pro Tag gecacht)
    with db.conn() as con:
        cached = {(r["league"], r["player_id"]): dict(r) for r in con.execute(
            "SELECT * FROM athlete_stats WHERE updated > ?", (now - 86400,))}
    need = [(r["league"], r["player_id"]) for r in rows if r["league"] in BASKETBALL and (r["league"], r["player_id"]) not in cached]
    with ThreadPoolExecutor(max_workers=config.HTTP_WORKERS) as ex:
        fetched = list(ex.map(lambda k: (k, _athlete_stats(*k)), need))
    new_stats = [{"league": k[0], "player_id": k[1], **s, "updated": now} for k, s in fetched if s]
    with db.conn() as con:
        db.upsert(con, "athlete_stats", new_stats, ("league", "player_id"))
    cached.update({(s["league"], s["player_id"]): s for s in new_stats})

    for r in rows:
        s = cached.get((r["league"], r["player_id"]))
        r["ppg"] = s["ppg"] if s else None
        r["mpg"] = s["mpg"] if s else None
        if r["league"] in BASKETBALL:
            r["impact"] = basketball_impact(s["ppg"], s["rpg"], s["apg"], s["spg"], s["bpg"], s["tpg"], s["mpg"]) if s else 0.5
        elif r["league"] in FOOTBALL:
            r["impact"] = football_impact(r["position"])
        else:
            r["impact"] = 0.0  # NHL/MLB: nur Anzeige
        fresh = r["date_ts"] is not None and now - r["date_ts"] < FRESH_DAYS * 86400
        # Langzeitausfälle (Injured Reserve) stecken bereits in der Form des Teams
        long_term = (r["status"] or "").lower() in ("injured reserve", "60-day-il", "out for season")
        r["weight"] = status_weight(r["status"]) if fresh and not long_term else 0.0

    with db.conn() as con:
        con.execute("DELETE FROM injuries")
        db.upsert(con, "injuries", rows, ("league", "player_id"))
    db.kv_set("last_injuries", now)
    progress(f"{len(rows)} Verletzungen geladen ({len(new_stats)} Spielerstatistiken neu)")
    return len(rows)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def match_team(name: str, espn_teams: list[str]) -> str | None:
    """Polymarket nutzt oft nur den Spitznamen ('Lakers'), ESPN den vollen Namen ('Los Angeles Lakers')."""
    n = _norm(name)
    if not n:
        return None
    for t in espn_teams:
        if _norm(t) == n:
            return t
    for t in espn_teams:
        nt = _norm(t)
        if nt.endswith(" " + n) or n.endswith(" " + nt) or nt.startswith(n + " "):
            return t
    return None


def load() -> pd.DataFrame:
    init()
    return db.df("SELECT * FROM injuries")


def team_adjustments(inj: pd.DataFrame | None = None) -> dict[tuple[str, str], dict]:
    """(league, ESPN-Team) -> {'points': Abzug, 'players': [...]}"""
    inj = load() if inj is None else inj
    out: dict[tuple[str, str], dict] = {}
    for (league, team), g in inj.groupby(["league", "team"]):
        g = g.assign(eff=g["impact"].fillna(0) * g["weight"].fillna(0)).sort_values("eff", ascending=False)
        pts = float(min(g["eff"].sum(), MAX_TEAM_ADJ))
        key_players = [
            f"{r.player} ({status_de(r.status)}{(', −' + f'{r.eff:.1f}'.replace('.', ',') + ' Pkt.') if r.eff >= 0.3 else ''})" for r in g.itertuples() if r.weight > 0
        ][:4]
        out[(league, team)] = {"points": pts, "players": key_players, "all": len(g)}
    return out


class Adjuster:
    """Liefert für ein Spiel die Punktabzüge beider Seiten und einen kurzen Hinweistext."""

    def __init__(self) -> None:
        self.adj = team_adjustments()
        self.teams: dict[str, list[str]] = {}
        for league, team in self.adj:
            self.teams.setdefault(league, []).append(team)

    def for_team(self, code: str, name: str) -> dict | None:
        if code not in self.teams:
            return None
        t = match_team(name, self.teams[code])
        return self.adj.get((code, t)) if t else None

    def game(self, code: str, a: str, b: str) -> tuple[float, float, str]:
        ia, ib = self.for_team(code, a), self.for_team(code, b)
        pa = ia["points"] if ia else 0.0
        pb = ib["points"] if ib else 0.0
        notes = []
        for name, info in ((a, ia), (b, ib)):
            if info and info["players"]:
                notes.append(f"{name}: " + ", ".join(info["players"]))
        return pa, pb, ". ".join(notes)


def summary_for_ui() -> pd.DataFrame:
    d = load()
    if d.empty:
        return d
    d["Datum"] = pd.to_datetime(d["date_ts"], unit="s", utc=True).dt.tz_convert("Europe/Berlin")
    d["eingepreist"] = d["weight"] == 0
    d["Abzug"] = d["impact"].fillna(0) * d["weight"].fillna(0)
    return d.sort_values(["Abzug", "date_ts"], ascending=False)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
