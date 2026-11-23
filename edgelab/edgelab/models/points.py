"""Punkte-Modell für Sportarten mit vielen Punkten (Basketball, American Football …).

Lineares Ridge-Modell: Punkte = Schnitt + Angriff(Team) + Abwehrschwäche(Gegner) + Heimvorteil.
Daraus ergeben sich erwartete Differenz (Spread) und Gesamtpunkte (Total); die Streuung wird aus den
Residuen geschätzt, Wahrscheinlichkeiten über die Normalverteilung.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


def norm_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


@dataclass
class PointsModel:
    mean: float
    off: dict[str, float]
    dfn: dict[str, float]
    home_adv: float
    sd_margin: float
    sd_total: float
    games: dict[str, float] = field(default_factory=dict)

    def known(self, name: str, min_games: float = 4) -> bool:
        return self.games.get(name, 0.0) >= min_games

    def expected(self, a: str, b: str, home: str | None) -> tuple[float, float]:
        pa = self.mean + self.off.get(a, 0) + self.dfn.get(b, 0) + (self.home_adv / 2 if home == "a" else -self.home_adv / 2 if home == "b" else 0)
        pb = self.mean + self.off.get(b, 0) + self.dfn.get(a, 0) + (self.home_adv / 2 if home == "b" else -self.home_adv / 2 if home == "a" else 0)
        return pa, pb

    def predict(self, a: str, b: str, home: str | None, adj_a: float = 0.0, adj_b: float = 0.0) -> dict:
        """adj_a/adj_b: erwartete Punkte, die A/B durch Ausfälle verlieren (siehe injuries.py)."""
        pa, pb = self.expected(a, b, home)
        pa, pb = pa - adj_a, pb - adj_b
        margin = pa - pb
        total = pa + pb
        sdm, sdt = self.sd_margin, self.sd_total

        def over(line: float) -> float:
            return 1 - norm_cdf((line - total) / sdt)

        def under(line: float) -> float:
            return norm_cdf((line - total) / sdt)

        def cover(side: str, line: float) -> float:
            mm = margin if side == "a" else -margin
            return 1 - norm_cdf((-line - mm) / sdm)

        return {
            "win_a": norm_cdf(margin / sdm),
            "margin_mean": margin,
            "total_mean": total,
            "pts_a": pa,
            "pts_b": pb,
            "over": over,
            "under": under,
            "cover": cover,
        }


def fit(games: pd.DataFrame, now_ts: int, half_life_days: float = 120.0, ridge: float = 4.0) -> PointsModel:
    g = games.dropna(subset=["score_a", "score_b"])
    g = g[g["start_ts"] <= now_ts]
    teams = pd.Index(pd.unique(pd.concat([g["side_a"], g["side_b"]])))
    n, t = len(g), len(teams)
    ia = teams.get_indexer(g["side_a"])
    ib = teams.get_indexer(g["side_b"])
    home = g["home"].to_numpy(object)
    ha = np.where(home == "a", 0.5, np.where(home == "b", -0.5, 0.0))
    w = 0.5 ** ((now_ts - g["start_ts"].to_numpy(float)) / 86400.0 / half_life_days)
    sa = g["score_a"].to_numpy(float)
    sb = g["score_b"].to_numpy(float)

    # Zeilen: Punkte von A, Punkte von B. Spalten: off[0..t), dfn[t..2t), home, mean
    rows = 2 * n
    X = np.zeros((rows, 2 * t + 2))
    r = np.arange(n)
    X[r, ia] = 1
    X[r, t + ib] = 1
    X[r, 2 * t] = ha
    X[n + r, ib] = 1
    X[n + r, t + ia] = 1
    X[n + r, 2 * t] = -ha
    X[:, 2 * t + 1] = 1
    yv = np.concatenate([sa, sb])
    wv = np.concatenate([w, w])
    Xw = X * wv[:, None]
    A = X.T @ Xw
    reg = np.full(2 * t + 2, ridge)
    reg[2 * t :] = 1e-6
    A[np.diag_indices_from(A)] += reg
    beta = np.linalg.solve(A, Xw.T @ yv)

    off, dfn = beta[:t], beta[t : 2 * t]
    hadv, mean = float(beta[2 * t]), float(beta[2 * t + 1])
    pred_a = X[:n] @ beta
    pred_b = X[n:] @ beta
    res_m = (sa - sb) - (pred_a - pred_b)
    res_t = (sa + sb) - (pred_a + pred_b)
    sw = np.sum(w)
    sd_m = float(np.sqrt(np.sum(w * res_m**2) / max(sw, 1e-9))) or 10.0
    sd_t = float(np.sqrt(np.sum(w * res_t**2) / max(sw, 1e-9))) or 15.0
    games_w = np.bincount(ia, weights=w, minlength=t) + np.bincount(ib, weights=w, minlength=t)
    names = list(teams)
    return PointsModel(
        mean=mean,
        off=dict(zip(names, off.tolist())),
        dfn=dict(zip(names, dfn.tolist())),
        home_adv=hadv,
        sd_margin=max(sd_m, 1.0),
        sd_total=max(sd_t, 1.0),
        games=dict(zip(names, games_w.tolist())),
    )
