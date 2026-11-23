import math

import numpy as np
import pandas as pd

from edgelab.models import elo, goals, points
from edgelab import betting


def _games(rows):
    return pd.DataFrame(rows, columns=["start_ts", "side_a", "side_b", "home", "score_a", "score_b", "result"])


def test_elo_prob_symmetry_and_home():
    assert math.isclose(elo.win_prob(1500, 1500, 0), 0.5)
    assert math.isclose(elo.win_prob(1600, 1500, 0) + elo.win_prob(1500, 1600, 0), 1.0)
    assert elo.win_prob(1500, 1500, 50) > 0.5


def test_elo_learns_stronger_side():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(400):
        a, b = ("Strong", "Weak") if i % 2 else ("Weak", "Strong")
        strong_wins = rng.random() < 0.8
        res = ("a" if a == "Strong" else "b") if strong_wins else ("b" if a == "Strong" else "a")
        rows.append((i * 3600, a, b, None, None, None, res))
    model = elo.fit(_games(rows), k=24, hfa=0)
    assert model.ratings["Strong"] > model.ratings["Weak"] + 100
    # Walk-forward-Prognosen liegen für jedes Spiel vor
    assert len(model.pregame) == 400
    p = model.predict("Strong", "Weak", home=None)
    assert 0.65 < p < 0.92


def test_elo_handles_draws():
    rows = [(i, "A", "B", "a", 1, 1, "draw") for i in range(50)]
    model = elo.fit(_games(rows), k=20, hfa=0)
    assert abs(model.ratings["A"] - model.ratings["B"]) < 5


def test_goal_model_recovers_strength():
    rng = np.random.default_rng(1)
    teams = ["T%d" % i for i in range(8)]
    strength = {t: 0.6 + 0.15 * i for i, t in enumerate(teams)}
    rows = []
    ts = 0
    for _ in range(12):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                ts += 3600
                hg = rng.poisson(1.35 * strength[h] / strength[a] ** 0.5)
                ag = rng.poisson(1.05 * strength[a] / strength[h] ** 0.5)
                res = "a" if hg > ag else "b" if ag > hg else "draw"
                rows.append((ts, h, a, "a", hg, ag, res))
    m = goals.fit(_games(rows), now_ts=ts)
    assert m.attack["T7"] > m.attack["T0"]
    pr = m.predict("T7", "T0", home="a")
    assert math.isclose(pr["a"] + pr["draw"] + pr["b"], 1.0, abs_tol=1e-6)
    assert pr["a"] > 0.6
    assert 0 < pr["over"](2.5) < 1
    assert math.isclose(pr["over"](2.5) + pr["under"](2.5), 1.0, abs_tol=1e-6)
    # Handicap: Favorit -0.5 entspricht Heimsieg
    assert math.isclose(pr["cover"]("a", -0.5), pr["a"], abs_tol=1e-6)


def test_points_model():
    rng = np.random.default_rng(2)
    teams = ["A", "B", "C", "D"]
    rating = {"A": 8, "B": 3, "C": -3, "D": -8}
    rows = []
    ts = 0
    for _ in range(60):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                ts += 3600
                sh = 110 + rating[h] / 2 + 1.5 + rng.normal(0, 9)
                sa = 110 + rating[a] / 2 - 1.5 + rng.normal(0, 9)
                rows.append((ts, h, a, "a", round(sh), round(sa), "a" if sh > sa else "b"))
    m = points.fit(_games(rows), now_ts=ts)
    pr = m.predict("A", "D", home="a")
    assert pr["win_a"] > 0.75
    assert 200 < pr["total_mean"] < 240
    assert math.isclose(pr["over"](pr["total_mean"]), 0.5, abs_tol=0.01)


def test_fee_ev_kelly():
    assert math.isclose(betting.fee_per_share(0.5, 0.05), 0.0125)
    # fairer Preis ohne Gebühr -> EV 0
    assert math.isclose(betting.ev(0.5, 0.5, 0.0), 0.0)
    assert betting.ev(0.6, 0.5, 0.05) > 0
    assert betting.ev(0.5, 0.5, 0.05) < 0
    assert betting.kelly(0.5, 0.5, 0.0) == 0
    assert math.isclose(betting.kelly(0.6, 0.5, 0.0), 0.2)
    assert betting.kelly(0.4, 0.5, 0.0) == 0
    assert math.isclose(betting.decimal_ev(0.5, 2.2), 0.1)
    assert math.isclose(betting.decimal_kelly(0.5, 3.0), 0.25)


def test_suspicious():
    from edgelab.predict import is_suspicious

    assert is_suspicious(0.42, 0.013)   # Außenseiter, Modell 30x höher
    assert is_suspicious(0.80, 0.50)    # > 25 Prozentpunkte
    assert not is_suspicious(0.55, 0.50)
    assert not is_suspicious(0.97, 0.92)


def test_props_distribution():
    from edgelab.props import avg_line, nb_over

    assert avg_line(24.3) == 24.5 and avg_line(24.8) == 24.5 and avg_line(7.0) == 7.5
    # Poisson-Fall: P(X > 0.5) = 1 - e^-mean
    assert math.isclose(nb_over(2.0, 2.0, 0.5), 1 - math.exp(-2), rel_tol=1e-6)
    # Mehr Streuung -> Außenseiter-Linie wahrscheinlicher
    assert nb_over(20, 60, 30.5) > nb_over(20, 21, 30.5)
    # Linie knapp unter dem Mittel -> > 50 %
    assert nb_over(24.3, 40, 23.5) > 0.5


def test_bo3_set_prob():
    from edgelab.games import bo3_set_prob

    for p in (0.2, 0.5, 0.73):
        s = bo3_set_prob(p)
        assert math.isclose(s * s * (3 - 2 * s), p, abs_tol=1e-9)
    s = bo3_set_prob(0.7)
    # 2:0 + 2:1 = Siegwahrscheinlichkeit
    assert math.isclose(s * s + 2 * s * s * (1 - s), 0.7, abs_tol=1e-9)
