"""Backtest: Wie gut sind die Modelle wirklich?

Für vergangene Spiele wird die Modellprognose *ohne Kenntnis des Ergebnisses* berechnet (Walk-forward:
Elo Spiel für Spiel, Tor-/Punktemodelle wöchentlich neu trainiert, nur mit Daten vor dem Spiel) und mit
dem Polymarket-Kurs beim Anpfiff (Schlusskurs) verglichen. Der Schlusskurs ist der härteste Maßstab:
Wer ihn dauerhaft schlägt, hat einen echten Vorteil.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config, db, ingest
from .betting import fee_per_share
from .modeling import load_games, train_pool
from .models import goals, points
from .families import excluded
from .predict import is_suspicious

THRESHOLDS = (0.02, 0.05, 0.10)


def _ll(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-4, 1 - 1e-4)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def _roi(p: np.ndarray, c: np.ndarray, y: np.ndarray, thr: float, fee_rate: float) -> dict:
    """Flat-Stake-Simulation: kaufe die Seite, bei der Modell − Schlusskurs > thr."""
    profits: list[float] = []
    for pi, ci, yi in zip(p, c, y):
        if is_suspicious(pi, ci):
            continue
        if pi - ci > thr:
            cost = ci + fee_per_share(ci, fee_rate)
            profits.append((yi - cost) / cost)
        elif ci - pi > thr:
            cb = 1 - ci
            cost = cb + fee_per_share(cb, fee_rate)
            profits.append(((1 - yi) - cost) / cost)
    n = len(profits)
    return {"bets": n, "roi": float(np.mean(profits)) if n else None}


def run_pool(pool: str, n_eval: int = 300, min_n: int = 10, progress=lambda s: None) -> dict | None:
    g = load_games(pool).reset_index(drop=True)
    if len(g) < 100:
        return None
    now = db.now()
    base = train_pool(pool, g, now)  # Elo walk-forward steckt in base.elo.pregame
    if base is None:
        return None
    g["elo_p"] = base.elo.pregame
    g["elo_n"] = base.elo.pregame_n
    g["y"] = (g["result"] == "a").astype(float)

    cand = g[(g.index >= int(len(g) * 0.4)) & g["token_a"].notna() & (g["start_ts"] < now - 3 * 3600)]
    cand = cand[cand["elo_n"] >= min_n].tail(n_eval).copy()
    if len(cand) < 30:
        return None

    # Wöchentliches Neutraining der Tor-/Punktemodelle nur mit Daten vor dem Spiel
    score_kind = "goals" if base.goals is not None else "points" if base.points else None
    cand["score_p"] = np.nan
    if score_kind:
        scored = g.dropna(subset=["score_a", "score_b"])
        cand["week"] = (cand["start_ts"] // (7 * 86400)).astype(int)
        for wk, part in cand.groupby("week"):
            t0 = int(wk * 7 * 86400)
            hist = scored[scored["start_ts"] < t0]
            if len(hist) < 100:
                continue
            per_code: dict = {}
            if score_kind == "goals":
                gm = goals.fit(hist, now_ts=t0)
            for idx, r in part.iterrows():
                if score_kind == "goals":
                    mdl = gm
                else:
                    if r["code"] not in per_code:
                        hc = hist[hist["code"] == r["code"]]
                        per_code[r["code"]] = points.fit(hc, now_ts=t0) if len(hc) >= 75 else None
                    mdl = per_code[r["code"]]
                    if mdl is None:
                        continue
                if not (mdl.known(r["side_a"]) and mdl.known(r["side_b"])):
                    continue
                home = r["home"] if isinstance(r["home"], str) else None
                pr = mdl.predict(r["side_a"], r["side_b"], home)
                if score_kind == "goals":
                    cand.at[idx, "score_p"] = pr["a"] if r["shape"] == "3way" else pr["a"] + 0.5 * pr["draw"]
                else:
                    cand.at[idx, "score_p"] = pr["win_a"]

    def model_p(r: pd.Series) -> float:
        if r["shape"] == "3way":
            return r["score_p"]  # Elo kennt kein Unentschieden -> nur Tormodell
        if not math.isnan(r["score_p"]):
            return 0.5 * r["elo_p"] + 0.5 * r["score_p"]
        return r["elo_p"]

    cand["model_p"] = cand.apply(model_p, axis=1)
    progress(f"Backtest {pool}: Schlusskurse für {len(cand)} Spiele laden …")
    closes = ingest.closing_prices(list(zip(cand["token_a"], cand["start_ts"].astype(int))))
    cand["close"] = cand["token_a"].map(closes)
    ev = cand.dropna(subset=["model_p", "close"])
    ev = ev[(ev["close"] > 0.01) & (ev["close"] < 0.99)]
    if len(ev) < 30:
        return None

    p, c, y = ev["model_p"].to_numpy(float), ev["close"].to_numpy(float), ev["y"].to_numpy(float)
    blend = 0.5 * p + 0.5 * c
    bins = np.clip((p * 10).astype(int), 0, 9)
    calib = [
        {"bin": f"{b * 10}-{b * 10 + 10}%", "model": float(p[bins == b].mean()), "actual": float(y[bins == b].mean()), "n": int((bins == b).sum())}
        for b in range(10)
        if (bins == b).sum() >= 5
    ]
    res = {
        "pool": pool,
        "family": base.family,
        "n": int(len(ev)),
        "from": int(ev["start_ts"].min()),
        "to": int(ev["start_ts"].max()),
        "logloss_model": _ll(p, y),
        "logloss_market": _ll(c, y),
        "logloss_blend": _ll(blend, y),
        "brier_model": float(np.mean((p - y) ** 2)),
        "brier_market": float(np.mean((c - y) ** 2)),
        "roi": {f"{t:.0%}": _roi(p, c, y, t, config.DEFAULT_FEE_RATE) for t in THRESHOLDS},
        "calibration": calib,
        "model_kind": "Poisson" if score_kind == "goals" and (ev["shape"] == "3way").all() else (
            "Elo+" + ("Poisson" if score_kind == "goals" else "Punkte") if score_kind else "Elo"
        ),
        "computed": now,
    }
    db.kv_set(f"backtest:{pool}", res)
    return res


def run_all(n_eval: int = 300, pools: list[str] | None = None, progress=lambda s: None) -> list[dict]:
    if pools is None:
        pools = [p for p, fm in db.df("SELECT pool, MIN(family) fam, COUNT(*) n FROM games GROUP BY pool HAVING n >= 100 ORDER BY n DESC")[["pool", "fam"]].itertuples(index=False) if not excluded(fm)]
    out = []
    for pool in pools:
        try:
            r = run_pool(pool, n_eval=n_eval, progress=progress)
        except Exception as e:  # noqa: BLE001
            progress(f"Backtest {pool} fehlgeschlagen: {e}")
            continue
        if r:
            out.append(r)
            progress(
                f"Backtest {pool}: n={r['n']} Log-Loss Modell {r['logloss_model']:.4f} vs Markt {r['logloss_market']:.4f}"
            )
    return out


def results() -> pd.DataFrame:
    rows = db.df("SELECT key, value FROM kv WHERE key LIKE 'backtest:%'")
    import json

    out = []
    for v in rows["value"]:
        r = json.loads(v)
        if excluded(r.get("family")):
            continue
        row = {
            "Pool": r["pool"],
            "Sportart": r["family"],
            "Modell": r.get("model_kind"),
            "Spiele": r["n"],
            "Log-Loss Modell": r["logloss_model"],
            "Log-Loss Markt": r["logloss_market"],
            "Brier Modell": r["brier_model"],
            "Brier Markt": r["brier_market"],
            "Modell besser?": r["logloss_model"] < r["logloss_market"],
        }
        for k, v2 in r["roi"].items():
            row[f"Wetten (Edge>{k})"] = v2["bets"]
            row[f"ROI (Edge>{k})"] = v2["roi"]
        out.append(row)
    return pd.DataFrame(out)


def detail(pool: str) -> dict | None:
    return db.kv_get(f"backtest:{pool}")
