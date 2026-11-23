"""Trainiert alle Modelle pro Rating-Pool (z. B. 'soccer', 'basketball', 'esports-cs2')."""
from __future__ import annotations

import logging
import pickle
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config, db
from .families import SCORE_FAMILIES, excluded
from .models import elo, goals, points

log = logging.getLogger(__name__)

MIN_GAMES_POOL = 40
MIN_SCORED_FOR_SCORE_MODEL = 150
GOAL_MAX_AVG_TOTAL = 14.0   # bis hierhin Poisson (Fußball ~2.7, Eishockey ~6, Baseball ~9)
POINTS_MIN_AVG_TOTAL = 20.0  # ab hier Normalverteilung (Basketball ~220, Football ~45, Handball ~55)
MOV_FAMILIES = {"basketball", "american-football", "rugby", "aussie-rules", "handball"}
MODEL_FILE = config.DATA_DIR / "models.pkl"
ELO_PER_POINT = 25.0


@dataclass
class PoolModels:
    pool: str
    family: str
    n_games: int
    elo: elo.EloModel
    elo_params: dict
    goals: goals.GoalModel | None = None
    # Punktemodelle je Liga (Punktniveau und Streuung unterscheiden sich stark, z. B. NBA vs. College)
    points: dict[str, points.PointsModel] = field(default_factory=dict)

    def points_for(self, code: str | None, a: str, b: str) -> points.PointsModel | None:
        m = self.points.get(code or "")
        return m if m is not None and m.known(a) and m.known(b) else None

    def goals_for(self, a: str, b: str) -> goals.GoalModel | None:
        return self.goals if self.goals is not None and self.goals.known(a) and self.goals.known(b) else None

    def win_prob(
        self, a: str, b: str, home: str | None, code: str | None = None, adj: tuple[float, float] = (0.0, 0.0)
    ) -> tuple[float | None, str]:
        """P(A gewinnt) für Zwei-Wege-Märkte (Unentschieden/Verlängerung zur Hälfte verteilt).
        adj = Punkte, die A bzw. B durch Ausfälle verlieren."""
        if not (self.elo.known(a) and self.elo.known(b)):
            return None, ""
        shift = (adj[1] - adj[0]) * ELO_PER_POINT
        pe = elo.win_prob(self.elo.rating(a), self.elo.rating(b), self.elo._hfa_for(home) + shift)
        g = self.goals_for(a, b)
        if g is not None:
            pr = g.predict(a, b, home)
            return 0.5 * pe + 0.5 * (pr["a"] + 0.5 * pr["draw"]), "Elo+Poisson"
        pt = self.points_for(code, a, b)
        if pt is not None:
            return 0.5 * pe + 0.5 * pt.predict(a, b, home, adj[0], adj[1])["win_a"], "Elo+Punkte"
        return pe, "Elo"


def load_games(pool: str | None = None) -> pd.DataFrame:
    sql = "SELECT * FROM games"
    params: tuple = ()
    if pool:
        sql += " WHERE pool=?"
        params = (pool,)
    return db.df(sql + " ORDER BY start_ts", params)


def train_pool(pool: str, g: pd.DataFrame, now_ts: int, retune: bool = False) -> PoolModels | None:
    if len(g) < MIN_GAMES_POOL:
        return None
    family = g["family"].iloc[0]
    has_home = g["home"].notna().any()
    use_mov = family in MOV_FAMILIES and g["score_a"].notna().mean() > 0.5
    params = db.kv_get(f"elo_params:{pool}")
    if retune or not params:
        k, h, ll = elo.tune(g, has_home=has_home, use_mov=use_mov)
        params = {"k": k, "hfa": h, "logloss": ll, "tuned": now_ts}
        db.kv_set(f"elo_params:{pool}", params)
    em = elo.fit(g, k=params["k"], hfa=params["hfa"], use_mov=use_mov)
    pm = PoolModels(pool=pool, family=family, n_games=len(g), elo=em, elo_params=params)

    scored = g.dropna(subset=["score_a", "score_b"])
    if family in SCORE_FAMILIES and len(scored) >= MIN_SCORED_FOR_SCORE_MODEL:
        avg_total = float((scored["score_a"] + scored["score_b"]).mean())
        if avg_total <= GOAL_MAX_AVG_TOTAL:
            pm.goals = goals.fit(scored, now_ts=now_ts)
        elif avg_total >= POINTS_MIN_AVG_TOTAL:
            for code, part in scored.groupby("code"):
                if len(part) >= MIN_SCORED_FOR_SCORE_MODEL // 2:
                    pm.points[str(code)] = points.fit(part, now_ts=now_ts)
    return pm


def train_all(progress=lambda s: None, retune: bool = False) -> dict[str, PoolModels]:
    now_ts = db.now()
    games = load_games()
    out: dict[str, PoolModels] = {}
    last_tune = db.kv_get("last_tune", 0)
    retune = retune or (now_ts - last_tune > 7 * 86400)
    for pool, g in games.groupby("pool"):
        if excluded(g["family"].iloc[0]):
            continue
        t = time.time()
        try:
            m = train_pool(str(pool), g, now_ts, retune=retune)
        except Exception as e:  # noqa: BLE001
            log.exception("Training %s fehlgeschlagen: %s", pool, e)
            continue
        if m:
            out[str(pool)] = m
            progress(f"Modell {pool}: {len(g)} Spiele ({time.time() - t:.1f}s)")
    if retune:
        db.kv_set("last_tune", now_ts)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(MODEL_FILE, "wb") as f:
        pickle.dump(out, f)
    db.kv_set("last_train", now_ts)
    return out


def load_models() -> dict[str, PoolModels]:
    if not MODEL_FILE.exists():
        return {}
    with open(MODEL_FILE, "rb") as f:
        return pickle.load(f)


def ratings_table(pm: PoolModels, top: int = 300) -> pd.DataFrame:
    rows = []
    for name, r in pm.elo.ratings.items():
        row = {"Name": name, "Elo": round(r), "Spiele": pm.elo.games.get(name, 0)}
        if pm.goals is not None and name in pm.goals.attack:
            row["Angriff (Tore)"] = round(pm.goals.attack[name], 2)
            row["Abwehr (Gegentore-Faktor)"] = round(pm.goals.defense[name], 2)
        for code, ptm in pm.points.items():
            if name in ptm.off:
                row["Liga"] = code
                row["Angriff (Pkt)"] = round(ptm.off[name], 1)
                row["Abwehr (Pkt, +=schwach)"] = round(ptm.dfn[name], 1)
        rows.append(row)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    return df.sort_values("Elo", ascending=False).head(top).reset_index(drop=True)


def summary(models: dict[str, PoolModels]) -> pd.DataFrame:
    rows = []
    for pool, m in sorted(models.items()):
        rows.append(
            {
                "Pool": pool,
                "Sportart": m.family,
                "Spiele": m.n_games,
                "Teams/Spieler": len(m.elo.ratings),
                "Modelle": ", ".join(
                    x for x, ok in (("Elo", True), ("Poisson", m.goals is not None), (f"Punkte ({', '.join(sorted(m.points))})", bool(m.points))) if ok
                ),
                "Elo K": m.elo_params.get("k"),
                "Heimvorteil (Elo)": m.elo_params.get("hfa"),
                "Log-Loss (Training)": None if m.elo_params.get("logloss") is None or np.isnan(m.elo_params.get("logloss", np.nan)) else round(m.elo_params["logloss"], 4),
            }
        )
    return pd.DataFrame(rows)
