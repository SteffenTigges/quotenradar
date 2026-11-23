"""Poisson-Tormodell mit Dixon-Coles-Korrektur (Fußball, Eishockey, Baseball …).

Jedes Team bekommt eine Angriffs- und eine Abwehrstärke, dazu ein Heimvorteil. Ältere Spiele zählen
weniger (Halbwertszeit), neue Teams werden zum Durchschnitt hin gezogen (Prior). Die Schätzung
läuft über die klassische iterative Maximum-Likelihood-Lösung (Maher), daher sehr schnell, auch
bei tausenden Teams aus vielen Ligen.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

MAX_GOALS = 15


@dataclass
class GoalModel:
    attack: dict[str, float]
    defense: dict[str, float]
    home_adv: float
    rho: float
    mean_goals: float
    games: dict[str, float] = field(default_factory=dict)  # gewichtete Spielanzahl je Team
    max_goals: int = MAX_GOALS

    def known(self, name: str, min_games: float = 4) -> bool:
        return self.games.get(name, 0.0) >= min_games

    def rates(self, a: str, b: str, home: str | None) -> tuple[float, float]:
        aa = self.attack.get(a, self.mean_goals)
        ab = self.attack.get(b, self.mean_goals)
        da = self.defense.get(a, 1.0)
        db = self.defense.get(b, 1.0)
        la = aa * db * (self.home_adv if home == "a" else 1.0)
        lb = ab * da * (self.home_adv if home == "b" else 1.0)
        return la, lb

    def matrix(self, a: str, b: str, home: str | None) -> np.ndarray:
        la, lb = self.rates(a, b, home)
        n = self.max_goals + 1
        k = np.arange(n)
        pa = np.exp(-la) * la**k / np.array([math.factorial(int(i)) for i in k])
        pb = np.exp(-lb) * lb**k / np.array([math.factorial(int(i)) for i in k])
        m = np.outer(pa, pb)
        r = self.rho
        m[0, 0] *= 1 - la * lb * r
        m[0, 1] *= 1 + la * r
        m[1, 0] *= 1 + lb * r
        m[1, 1] *= 1 - r
        m = np.clip(m, 0, None)
        return m / m.sum()

    def predict(self, a: str, b: str, home: str | None) -> dict:
        m = self.matrix(a, b, home)
        n = m.shape[0]
        i, j = np.indices(m.shape)
        diff = i - j
        tot = i + j
        la, lb = self.rates(a, b, home)

        def over(line: float) -> float:
            return float(m[tot > line].sum() / max(1e-12, 1 - m[tot == line].sum()))

        def under(line: float) -> float:
            return float(m[tot < line].sum() / max(1e-12, 1 - m[tot == line].sum()))

        def cover(side: str, line: float) -> float:
            """P(Seite gewinnt mit Handicap `line`), Push (bei ganzen Linien) herausgerechnet."""
            d = diff if side == "a" else -diff
            win = m[d + line > 0].sum()
            push = m[d + line == 0].sum()
            return float(win / max(1e-12, 1 - push))

        def team_over(side: str, line: float) -> float:
            g = i if side == "a" else j
            return float(m[g > line].sum())

        return {
            "a": float(m[diff > 0].sum()),
            "draw": float(m[diff == 0].sum()),
            "b": float(m[diff < 0].sum()),
            "btts": float(m[(i > 0) & (j > 0)].sum()),
            "over": over,
            "under": under,
            "cover": cover,
            "team_over": team_over,
            "xg_a": la,
            "xg_b": lb,
            "_n": n,
        }


def _dc_loglik(rho: float, x: np.ndarray, y: np.ndarray, la: np.ndarray, lb: np.ndarray, w: np.ndarray) -> float:
    tau = np.ones_like(la)
    tau = np.where((x == 0) & (y == 0), 1 - la * lb * rho, tau)
    tau = np.where((x == 0) & (y == 1), 1 + la * rho, tau)
    tau = np.where((x == 1) & (y == 0), 1 + lb * rho, tau)
    tau = np.where((x == 1) & (y == 1), 1 - rho, tau)
    if np.any(tau <= 0):
        return -np.inf
    return float(np.sum(w * np.log(tau)))


def fit(
    games: pd.DataFrame,
    now_ts: int,
    half_life_days: float = 150.0,
    prior_games: float = 3.0,
    iterations: int = 60,
) -> GoalModel:
    g = games.dropna(subset=["score_a", "score_b"])
    g = g[g["start_ts"] <= now_ts]
    teams = pd.Index(pd.unique(pd.concat([g["side_a"], g["side_b"]])))
    ia = teams.get_indexer(g["side_a"])
    ib = teams.get_indexer(g["side_b"])
    x = g["score_a"].to_numpy(float)
    y = g["score_b"].to_numpy(float)
    home = g["home"].to_numpy(object)
    ha = (home == "a").astype(float)
    hb = (home == "b").astype(float)
    age_days = (now_ts - g["start_ts"].to_numpy(float)) / 86400.0
    w = 0.5 ** (age_days / half_life_days)
    n = len(teams)
    if n == 0:
        return GoalModel({}, {}, 1.0, 0.0, 1.3)

    mu0 = float(np.sum(w * (x + y)) / max(1e-9, 2 * np.sum(w)))
    att = np.full(n, mu0)
    dfn = np.ones(n)
    gamma = 1.2
    m = prior_games

    def bc(idx, vals):
        return np.bincount(idx, weights=vals, minlength=n)

    s_att = bc(ia, w * x) + bc(ib, w * y)
    s_def = bc(ia, w * y) + bc(ib, w * x)
    games_w = bc(ia, w) + bc(ib, w)
    for _ in range(iterations):
        ga = gamma**ha
        gb = gamma**hb
        e_att = bc(ia, w * dfn[ib] * ga) + bc(ib, w * dfn[ia] * gb)
        att = (s_att + m * mu0) / (e_att + m)
        e_def = bc(ia, w * att[ib] * gb) + bc(ib, w * att[ia] * ga)
        dfn = (s_def + m * mu0) / (e_def + m * mu0)
        # Normierung: durchschnittliche Abwehr = 1
        c = float(np.sum(games_w * dfn) / max(1e-9, np.sum(games_w)))
        dfn /= c
        att *= c
        hm = (ha > 0) | (hb > 0)
        if hm.any():
            num = np.sum((w * ha * x)) + np.sum((w * hb * y))
            den = np.sum(w * ha * att[ia] * dfn[ib]) + np.sum(w * hb * att[ib] * dfn[ia])
            gamma = float(np.clip(num / max(1e-9, den), 0.8, 1.6)) if den > 0 else 1.0
        else:
            gamma = 1.0

    la = att[ia] * dfn[ib] * gamma**ha
    lb = att[ib] * dfn[ia] * gamma**hb
    best_rho, best_ll = 0.0, -np.inf
    for rho in np.linspace(-0.2, 0.1, 31):
        ll = _dc_loglik(rho, x, y, la, lb, w)
        if ll > best_ll:
            best_rho, best_ll = float(rho), ll

    names = list(teams)
    return GoalModel(
        attack=dict(zip(names, att.tolist())),
        defense=dict(zip(names, dfn.tolist())),
        home_adv=gamma,
        rho=best_rho,
        mean_goals=mu0,
        games=dict(zip(names, games_w.tolist())),
    )
