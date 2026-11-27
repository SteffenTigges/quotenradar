"""Erzeugt die Datei data.json, aus der die Web-App liest (wird von GitHub Actions stündlich aufgerufen).

    python -m edgelab export --out ../web/data.json
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest, darts, fx, games, injuries, whales
from .families import SPORT_NAME, WALLET_DE, league_tier, odds_link, outcome_de, parse_sides
from .injuries import status_de

MAX_GAMES = 400
SPORT_ORDER = ["soccer", "basketball", "darts", "hockey", "american-football", "tennis", "handball", "baseball", "volleyball", "cricket"]


def gkey(a: str, b: str) -> str:
    n = lambda x: re.sub(r"[^a-z0-9]", "", (x or "").lower())  # noqa: E731
    return "|".join(sorted([n(a), n(b)]))


def _r(x, n: int = 4):
    if x is None:
        return None
    try:
        if pd.isna(x):
            return None
    except (TypeError, ValueError):
        pass
    return round(float(x), n)


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.floating):
        o = float(o)
    if isinstance(o, float):
        return None if math.isnan(o) else round(o, 4)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def _iso(ts: int) -> str:
    return pd.Timestamp(int(ts), unit="s", tz="UTC").tz_convert("Europe/Berlin").strftime("%Y-%m-%dT%H:%M")


def export_games(hours: int = 48) -> list[dict]:
    out = []
    for g in games.build(hours):
        if not (g["model"] or g.get("books")):
            continue
        cons = games.books_consensus(g)
        d = {
            "id": f"{g['code']}-{gkey(g['a'], g['b'])}-{g['start']}",
            "sport": g["family"], "sportName": SPORT_NAME.get(g["family"], g["family"]), "league": g["league"],
            "a": g["a"], "b": g["b"], "start": _iso(g["start"]),
            "model": g["model"] or None, "books": cons,  # Buchmacher-Konsens (Marge herausgerechnet)
            "method": g.get("method"), "expected": g.get("expected"), "injuries": g.get("injuries"),
            "groups": g.get("groups") or [],
            "props": [dict(p, status=status_de(p["status"]) if p.get("status") else None) for p in g.get("props") or []],
            "odds": games.books_rows(g) if g.get("books") else [], "bookList": games.book_list(g) if g.get("books") else [],
            "link": odds_link(f"{g['a']} vs. {g['b']}", g["family"]), "key": gkey(g["a"], g["b"]),
            "weight": g.get("liquidity") or 0,
        }
        d["tier"], d["rank"] = league_tier(g["family"], g["league"])
        if g.get("darts"):
            keep = ("avg", "first9", "s180", "checkout", "legs_won", "high_co")
            d["darts"] = {"format": g["darts"].get("format")}
            if g["darts"].get("a") and g["darts"].get("b"):  # Spielerstatistik nur, wenn Darts Orakel erreichbar war
                d["darts"]["a"] = {k: g["darts"]["a"].get(k) for k in keep}
                d["darts"]["b"] = {k: g["darts"]["b"].get(k) for k in keep}
        out.append(_clean(d))
    rank = {s: i for i, s in enumerate(SPORT_ORDER)}
    # Wichtigste zuerst behalten: wichtige Ligen, mit Buchmacherquoten, große Sportarten, hohe Aufmerksamkeit
    out.sort(key=lambda d: (d["tier"], 0 if d["odds"] else 1, rank.get(d["sport"], 99), -(d["weight"] or 0)))
    out = out[:MAX_GAMES]
    out.sort(key=lambda d: d["start"])
    for d in out:
        d.pop("weight", None)
    return out


def export_whales(hours: int = 24) -> list[dict]:
    f = whales.feed(hours)
    if f.empty:
        return []
    f = f[(f["price"] < 0.95) & (f["price"] > 0.05) & (f["side"] == "BUY") & (f["eur"] >= 1000)]
    out = []
    for x in f.head(250).itertuples():
        sides = parse_sides(x.title or "")
        live = bool(pd.notna(x.game_start) and x.ts > x.game_start)
        out.append(_clean({
            "time": x.time.strftime("%Y-%m-%dT%H:%M"), "eur": _r(x.eur, 0), "sport": x.family, "sportName": SPORT_NAME.get(x.family, x.family),
            "match": " – ".join(sides) if sides else x.title, "key": gkey(*sides) if sides else None,
            "pick": outcome_de(x.outcome, x.title), "odds": _r(1 / x.price, 2) if x.price else None,
            "wallet": WALLET_DE.get(x.label, "Unbekannt"), "walletProfit": _r(x.pnl_eur, 0), "model": _r(x.model_prob), "price": _r(x.price, 3),
            "live": live,
        }))
    return out


def export_injuries() -> list[dict]:
    d = injuries.summary_for_ui()
    if d.empty:
        return []
    d = d[d["Abzug"] > 0].head(60)
    return [_clean({"league": x.league.upper(), "team": x.team, "player": x.player, "status": status_de(x.status),
                    "date": x.Datum.strftime("%Y-%m-%d"), "points": _r(x.Abzug, 1), "note": (x.comment or "")[:220]})
            for x in d.itertuples()]


def export_backtest() -> list[dict]:
    b = backtest.results()
    if b.empty:
        return []
    out = []
    for x in b.sort_values("Spiele", ascending=False).itertuples(index=False):
        pool, fam = x[0], x[1]
        out.append(_clean({"pool": pool, "sport": fam, "sportName": SPORT_NAME.get(fam, fam) + (" (Frauen)" if pool.endswith("-w") else ""),
                           "games": int(x[3]), "model": _r(x[4]), "market": _r(x[5])}))
    return out


def run(out_path: str, hours: int = 48, progress=lambda s: None) -> dict:
    t = time.time()
    data = {
        "updated": pd.Timestamp.now(tz="Europe/Berlin").strftime("%Y-%m-%dT%H:%M"),
        "eurUsd": _r(fx.eur_usd(), 4),
        "games": export_games(hours),
        "whales": export_whales(),
        "injuries": export_injuries(),
        "backtest": export_backtest(),
        "dartsFormats": {k: v for k, v in darts.FORMATS.items() if v},
    }
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    progress(f"Export: {len(data['games'])} Spiele, {len(data['whales'])} Großwetten, {p.stat().st_size // 1024} KB ({time.time() - t:.0f}s)")
    return data
