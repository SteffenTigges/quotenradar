"""Elo-Ratings für jede Sportart (Teams oder Einzelspieler).

Walk-forward: Für jedes Spiel wird die Prognose *vor* dem Spiel gespeichert (für den Backtest),
danach werden die Ratings aktualisiert.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BASE = 1500.0


def win_prob(ra: float, rb: float, hfa: float) -> float:
    """Wahrscheinlichkeit, dass A gewinnt. hfa = Heimvorteil in Elo-Punkten zugunsten von A."""
    return 1.0 / (1.0 + 10 ** (-(ra - rb + hfa) / 400.0))


@dataclass
class EloModel:
    k: float
    hfa: float
    use_mov: bool
    ratings: dict[str, float] = field(default_factory=dict)
    games: dict[str, int] = field(default_factory=dict)
    last_seen: dict[str, int] = field(default_factory=dict)
    pregame: list[float] = field(default_factory=list)  # P(A gewinnt) vor jedem Spiel, in Eingabereihenfolge
    pregame_n: list[int] = field(default_factory=list)  # min. bisherige Spiele beider Seiten vor dem Spiel

    def rating(self, name: str) -> float:
        return self.ratings.get(name, BASE)

    def _hfa_for(self, home: str | None) -> float:
        return self.hfa if home == "a" else -self.hfa if home == "b" else 0.0

    def predict(self, a: str, b: str, home: str | None) -> float:
        return win_prob(self.rating(a), self.rating(b), self._hfa_for(home))

    def known(self, name: str, min_games: int = 5) -> bool:
        return self.games.get(name, 0) >= min_games

    def update(self, a: str, b: str, home: str | None, result: str, margin: float | None, ts: int = 0) -> float:
        ra, rb = self.rating(a), self.rating(b)
        p = win_prob(ra, rb, self._hfa_for(home))
        s = 1.0 if result == "a" else 0.0 if result == "b" else 0.5
        # Neue Teams/Spieler lernen schneller
        ka = self.k * (1 + 2 * math.exp(-self.games.get(a, 0) / 8))
        kb = self.k * (1 + 2 * math.exp(-self.games.get(b, 0) / 8))
        mult = 1.0
        if self.use_mov and margin is not None and result != "draw":
            diff = (ra - rb + self._hfa_for(home)) * (1 if result == "a" else -1)
            mult = math.log(abs(margin) + 1) * 2.2 / (diff * 0.001 + 2.2)
            mult = min(max(mult, 0.5), 3.0)
        self.ratings[a] = ra + ka * mult * (s - p)
        self.ratings[b] = rb - kb * mult * (s - p)
        self.games[a] = self.games.get(a, 0) + 1
        self.games[b] = self.games.get(b, 0) + 1
        self.last_seen[a] = ts
        self.last_seen[b] = ts
        return p


def fit(games: pd.DataFrame, k: float = 24.0, hfa: float = 0.0, use_mov: bool = False) -> EloModel:
    """games: Spalten start_ts, side_a, side_b, home, score_a, score_b, result (chronologisch sortiert)."""
    m = EloModel(k=k, hfa=hfa, use_mov=use_mov)
    g = games.sort_values("start_ts", kind="stable")
    for row in g.itertuples(index=False):
        margin = None
        if use_mov and row.score_a is not None and row.score_b is not None and not (
            pd.isna(row.score_a) or pd.isna(row.score_b)
        ):
            margin = float(row.score_a) - float(row.score_b)
        home = row.home if isinstance(row.home, str) else None
        m.pregame_n.append(min(m.games.get(row.side_a, 0), m.games.get(row.side_b, 0)))
        m.pregame.append(m.update(row.side_a, row.side_b, home, row.result, margin, int(row.start_ts)))
    return m


def log_loss(probs: np.ndarray, outcomes: np.ndarray) -> float:
    p = np.clip(probs, 1e-6, 1 - 1e-6)
    return float(-np.mean(outcomes * np.log(p) + (1 - outcomes) * np.log(1 - p)))


def tune(games: pd.DataFrame, has_home: bool, use_mov: bool) -> tuple[float, float, float]:
    """Sucht K und Heimvorteil mit dem kleinsten Log-Loss (erste 30 % = Einschwingphase).
    Unentschieden werden für die Bewertung ignoriert."""
    g = games.sort_values("start_ts", kind="stable")
    outcomes = np.where(g["result"].to_numpy() == "a", 1.0, np.where(g["result"].to_numpy() == "b", 0.0, np.nan))
    burn = int(len(g) * 0.3)
    mask = ~np.isnan(outcomes)
    mask[:burn] = False
    if mask.sum() < 30:
        return 24.0, (50.0 if has_home else 0.0), float("nan")
    best = (24.0, 0.0, float("inf"))
    for k in (10, 16, 24, 32, 48):
        for h in ((0, 30, 60, 90) if has_home else (0,)):
            m = fit(g, k=k, hfa=h, use_mov=use_mov)
            ll = log_loss(np.array(m.pregame)[mask], outcomes[mask])
            if ll < best[2]:
                best = (float(k), float(h), ll)
    return best
