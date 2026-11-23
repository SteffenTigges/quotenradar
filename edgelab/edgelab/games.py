"""Spielkarten: pro Partie Siegwahrscheinlichkeiten (Modell vs. Markt) und ein Wettangebot wie bei
Buchmachern (Tipico, Betano, Winamax …) – mit fairer Quote aus dem Modell und, wo Polymarket den
Markt anbietet, dem echten Preis und EV.

Märkte je Sportart:
- Fußball:      1X2, Doppelte Chance, Unentschieden keine Wette, Über/Unter 1,5/2,5/3,5, Beide treffen,
                Team trifft, Zu-Null-Sieg, Sieg & beide treffen, Handicap, genaues Ergebnis
- Eishockey:    Sieger inkl. Verlängerung, 3-Weg nach 60 Min., Über/Unter, Puckline, Beide treffen
- Baseball:     Sieger, Über/Unter Runs, Runline
- Basketball/Football: Sieger, Handicap (faire Linie), Gesamtpunkte, Team-Punkte, Siegabstand,
                NBA-Spielerwetten (Punkte, Rebounds, Assists, Dreier, P+R+A)
- Tennis/Esports (Best of 3): Sieger, Satz-/Map-Ergebnis 2:0 / 2:1, Über/Unter 2,5 Sätze/Maps
- alle anderen: Sieger
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import bookmakers, darts, db, injuries, predict
from .families import excluded, is_neutral, parse_sides
from .modeling import PoolModels, load_models

BO3_FAMILIES = {"tennis", "esports", "table-tennis"}


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().replace(".", "").split())


def _home(family: str, ordering: str | None) -> str | None:
    if is_neutral(family) or ordering not in ("home", "away"):
        return None
    return "a" if ordering == "home" else "b"


def odds(p: float | None) -> float | None:
    return round(1 / p, 2) if p and p > 0 else None


def item(label: str, p: float) -> dict:
    return {"label": label, "p": round(float(p), 4), "odds": odds(p)}


def bo3_set_prob(p_match: float) -> float:
    """Aus P(Match) bei Best-of-3 die Satz-/Map-Wahrscheinlichkeit s lösen: p = s²(3 − 2s)."""
    lo, hi = 0.0, 1.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if mid * mid * (3 - 2 * mid) < p_match:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def fl(x: float) -> str:
    """Linie deutsch formatiert: 2.5 -> '2,5'."""
    return f"{x:g}".replace(".", ",")


def half_line(x: float) -> float:
    return math.floor(x) + 0.5


# ---------------------------------------------------------------- Märkte je Modell


def goal_markets(g: dict, a: str, b: str, family: str) -> list[dict]:
    m = g["_m"]
    i = m.shape[0]
    pa, pd_, pb = g["a"], g["draw"], g["b"]
    exp_total = g["xg_a"] + g["xg_b"]
    groups = []
    if family == "soccer":
        groups.append({"group": "Ergebnis (1X2)", "items": [item(f"Sieg {a}", pa), item("Unentschieden", pd_), item(f"Sieg {b}", pb)]})
        groups.append({"group": "Doppelte Chance", "items": [item(f"{a} oder X", pa + pd_), item(f"{a} oder {b}", pa + pb), item(f"X oder {b}", pd_ + pb)]})
        groups.append({"group": "Unentschieden keine Wette", "items": [item(a, pa / (pa + pb)), item(b, pb / (pa + pb))]})
        lines = [1.5, 2.5, 3.5]
    else:
        lines = sorted({half_line(exp_total) - 1, half_line(exp_total), half_line(exp_total) + 1})
        if family == "hockey":
            groups.append({"group": "Sieger inkl. Verlängerung", "items": [item(a, pa + pd_ * 0.5), item(b, pb + pd_ * 0.5)]})
            groups.append({"group": "3-Weg nach 60 Minuten", "items": [item(a, pa), item("Unentschieden", pd_), item(b, pb)]})
        else:
            groups.append({"group": "Sieger", "items": [item(a, pa + pd_ * 0.5), item(b, pb + pd_ * 0.5)]})
    unit = "Tore" if family in ("soccer", "hockey") else "Runs" if family == "baseball" else "Punkte"
    groups.append({"group": f"Über/Unter {unit}", "items": sum(([item(f"Über {fl(l)}", g["over"](l)), item(f"Unter {fl(l)}", g["under"](l))] for l in lines), [])})
    groups.append({"group": "Beide treffen", "items": [item("Ja", g["btts"]), item("Nein", 1 - g["btts"])]})
    if family == "soccer":
        groups.append({"group": "Team trifft", "items": [item(f"{a} trifft", g["team_over"]("a", 0.5)), item(f"{b} trifft", g["team_over"]("b", 0.5)),
                                                          item(f"{a} 2+ Tore", g["team_over"]("a", 1.5)), item(f"{b} 2+ Tore", g["team_over"]("b", 1.5))]})
        ii, jj = np.indices(m.shape)
        win_nil_a = float(m[(ii > jj) & (jj == 0)].sum())
        win_nil_b = float(m[(jj > ii) & (ii == 0)].sum())
        win_btts_a = float(m[(ii > jj) & (jj > 0)].sum())
        win_btts_b = float(m[(jj > ii) & (ii > 0)].sum())
        groups.append({"group": "Spezialwetten", "items": [item(f"{a} gewinnt zu null", win_nil_a), item(f"{b} gewinnt zu null", win_nil_b),
                                                            item(f"{a} gewinnt & beide treffen", win_btts_a), item(f"{b} gewinnt & beide treffen", win_btts_b)]})
        groups.append({"group": "Handicap", "items": [item(f"{a} −1,5", g["cover"]("a", -1.5)), item(f"{b} +1,5", g["cover"]("b", 1.5)),
                                                       item(f"{b} −1,5", g["cover"]("b", -1.5)), item(f"{a} +1,5", g["cover"]("a", 1.5))]})
        scores = sorted(((float(m[x, y]), f"{x}:{y}") for x in range(min(i, 6)) for y in range(min(i, 6))), reverse=True)[:6]
        groups.append({"group": "Genaues Ergebnis (Top 6)", "items": [item(s, p) for p, s in scores]})
    else:
        groups.append({"group": "Puckline" if family == "hockey" else "Runline", "items": [item(f"{a} −1,5", g["cover"]("a", -1.5)), item(f"{b} +1,5", g["cover"]("b", 1.5)),
                                                                                        item(f"{b} −1,5", g["cover"]("b", -1.5)), item(f"{a} +1,5", g["cover"]("a", 1.5))]})
    return groups


def points_markets(pr: dict, a: str, b: str, p_win_a: float) -> list[dict]:
    margin, total = pr["margin_mean"], pr["total_mean"]
    fav, dog, fm = (a, b, margin) if margin >= 0 else (b, a, -margin)
    fav_side = "a" if fav == a else "b"
    line = half_line(fm) if fm >= 1 else 1.5
    groups = [
        {"group": "Sieger", "items": [item(a, p_win_a), item(b, 1 - p_win_a)]},
        {"group": "Handicap (faire Linie)", "items": [item(f"{fav} −{fl(line)}", pr["cover"](fav_side, -line)),
                                                      item(f"{dog} +{fl(line)}", 1 - pr["cover"](fav_side, -line))]},
    ]
    tl = half_line(total)
    groups.append({"group": f"Gesamtpunkte (erwartet {total:.0f})", "items": sum(([item(f"Über {fl(l)}", pr["over"](l)), item(f"Unter {fl(l)}", pr["under"](l))] for l in (tl - 5, tl, tl + 5)), [])})
    ta, tb = half_line(pr["pts_a"]), half_line(pr["pts_b"])
    sd_team = max(pr.get("sd_team", 0) or 0, 1)
    from .models.points import norm_cdf

    groups.append({"group": "Team-Punkte", "items": [item(f"{a} über {fl(ta)}", 1 - norm_cdf((ta - pr["pts_a"]) / sd_team)),
                                                     item(f"{b} über {fl(tb)}", 1 - norm_cdf((tb - pr["pts_b"]) / sd_team))]})
    big = 10 if total > 120 else 7
    groups.append({"group": "Siegabstand", "items": [item(f"{a} mit {big}+", pr["cover"]("a", -(big - 0.5))), item(f"{a} mit 1–{big - 1}", p_win_a - pr["cover"]("a", -(big - 0.5))),
                                                     item(f"{b} mit 1–{big - 1}", (1 - p_win_a) - pr["cover"]("b", -(big - 0.5))), item(f"{b} mit {big}+", pr["cover"]("b", -(big - 0.5)))]})
    return groups


def bo3_markets(p: float, a: str, b: str, unit: str) -> list[dict]:
    s = bo3_set_prob(p)
    return [
        {"group": "Sieger", "items": [item(a, p), item(b, 1 - p)]},
        {"group": f"Ergebnis in {unit}", "items": [item(f"{a} 2:0", s * s), item(f"{a} 2:1", 2 * s * s * (1 - s)),
                                                   item(f"{b} 2:1", 2 * (1 - s) ** 2 * s), item(f"{b} 2:0", (1 - s) ** 2)]},
        {"group": f"Über/Unter {unit}", "items": [item("Über 2,5", 2 * s * (1 - s)), item("Unter 2,5", 1 - 2 * s * (1 - s))]},
    ]


# ---------------------------------------------------------------- Spiele aufbauen


def _mid(r: dict, idx: int) -> float | None:
    bb, ba = r.get("best_bid"), r.get("best_ask")
    if bb is not None and ba is not None and 0 < bb < ba < 1 and ba - bb <= 0.1:
        mid = (bb + ba) / 2
    else:
        mid = r.get("price0")
    if mid is None:
        return None
    return mid if idx == 0 else 1 - mid


def build(hours: int = 48, with_props: bool = True, progress=lambda s: None) -> list[dict]:
    models = load_models()
    now = db.now()
    with db.conn() as con:
        meta = {r["code"]: dict(r) for r in con.execute("SELECT code, name, pool, family, ordering FROM sports")}
        ml = [dict(r) for r in con.execute(
            "SELECT * FROM markets WHERE closed=0 AND is_sports=1 AND market_type='moneyline' AND game_start > ? AND game_start < ?",
            (now, now + hours * 3600))]
    try:
        adjuster = injuries.Adjuster()
    except Exception:  # noqa: BLE001
        adjuster = None
    edges = predict.edges()
    book_odds = bookmakers.load() if bookmakers.enabled() else None
    pm_props = None
    games: dict[tuple, dict] = {}

    # 1) Spiele aus den Sieger-Märkten zusammensetzen
    for r in ml:
        sm = meta.get(r["code"])
        if not sm or excluded(sm["family"]):
            continue
        yes_no = _norm(r["outcome0"]) == "yes"
        if yes_no:
            sides = parse_sides(r["event_title"] or "")
        else:
            sides = (r["outcome0"], r["outcome1"])
            if not parse_sides(r["event_title"] or ""):  # z. B. "Wer gewinnt die Saison-Serie"
                continue
        if not sides or "series" in _norm(r["question"]) or "season" in _norm(r["question"]):
            continue
        key = (r["code"], _norm(sides[0]), _norm(sides[1]), pd.Timestamp(r["game_start"], unit="s").strftime("%Y-%m-%d"))
        g = games.setdefault(key, {"code": r["code"], "league": sm["name"], "family": sm["family"], "pool": sm["pool"],
                                   "ordering": sm["ordering"], "a": sides[0], "b": sides[1], "start": r["game_start"],
                                   "slug": r["event_slug"], "title": r["event_title"], "market": {}, "liquidity": 0.0})
        g["liquidity"] += r["liquidity"] or 0
        if yes_no:
            gi = _norm(r["group_item"])
            k = "draw" if gi.startswith("draw") or "draw" in _norm(r["question"]) else "a" if gi == _norm(sides[0]) else "b" if gi == _norm(sides[1]) else None
            if k:
                g["market"][k] = _mid(r, 0)
        else:
            g["market"]["a"] = _mid(r, 0)
            g["market"]["b"] = _mid(r, 1)

    out = []
    for key, g in games.items():
        pm_: PoolModels | None = models.get(g["pool"])
        mk = g["market"]
        tot = sum(v for v in mk.values() if v)
        if tot:
            g["market"] = {k: round(v / tot, 4) for k, v in mk.items() if v}
        a, b, fam = g["a"], g["b"], g["family"]
        if fam == "darts" and ":" in (g.get("title") or ""):
            g["league"] = g["title"].split(":")[0].strip()  # Turnier statt nur "Darts"
        home = _home(fam, g["ordering"])
        g["home"] = home
        adj, note = (0.0, 0.0), ""
        if adjuster is not None and g["code"] in injuries.LEAGUES:
            pa, pb, note = adjuster.game(g["code"], a, b)
            adj = (pa, pb)
        g["injuries"] = note or None
        g["model"], g["groups"], g["method"] = {}, [], None
        if pm_ is not None:
            gm = pm_.goals_for(a, b)
            pt = pm_.points_for(g["code"], a, b)
            if gm is not None:
                pr = gm.predict(a, b, home)
                pr["_m"] = gm.matrix(a, b, home)
                p2, _ = pm_.win_prob(a, b, home, g["code"], adj)
                if fam == "soccer":
                    g["model"] = {"a": pr["a"], "draw": pr["draw"], "b": pr["b"]}
                else:
                    g["model"] = {"a": p2, "b": 1 - p2} if p2 is not None else {}
                g["groups"] = goal_markets(pr, a, b, fam)
                g["method"] = "Elo + Poisson"
                xa, xb = fl(round(pr["xg_a"], 1)), fl(round(pr["xg_b"], 1))
                g["expected"] = f"{xa} : {xb} Tore" if fam in ("soccer", "hockey") else f"{xa} : {xb}"
            elif fam == "soccer":
                pass  # ohne Tormodell kein Unentschieden -> keine Modellprognose, nur Markt
            elif fam == "darts" and darts.stats(a) and darts.stats(b):
                sa, sb = darts.stats(a), darts.stats(b)
                sets = darts.sets_to(g.get("title") or g["league"], g.get("start"))
                if sets:
                    p = darts.match_prob_sets(sa["avg"], sb["avg"], sets)
                    g["groups"] = darts.markets_sets(a, b, sa["avg"], sb["avg"], sets)
                    fmt = f"Best of {2 * sets - 1} Sätze"
                else:
                    k = darts.first_to(g.get("title") or g["league"])
                    p = darts.match_prob(sa["avg"], sb["avg"], k)
                    g["groups"] = darts.markets(a, b, sa["avg"], sb["avg"], k)
                    fmt = f"Best of {2 * k - 1} Legs" if k else None
                g["model"], g["method"] = {"a": p, "b": 1 - p}, "Average-Modell"
                g["darts"] = {"a": sa, "b": sb, "format": fmt}
            else:
                p2, method = pm_.win_prob(a, b, home, g["code"], adj)
                if p2 is not None:
                    g["model"] = {"a": p2, "b": 1 - p2}
                    g["method"] = method
                    if pt is not None:
                        pr = pt.predict(a, b, home, adj[0], adj[1])
                        pr["sd_team"] = pt.sd_total / math.sqrt(2)
                        g["groups"] = points_markets(pr, a, b, p2)
                        g["expected"] = f"{pr['pts_a']:.0f} : {pr['pts_b']:.0f} Punkte"
                    elif fam in BO3_FAMILIES:
                        g["groups"] = bo3_markets(p2, a, b, "Sätzen" if fam != "esports" else "Maps")
                    else:
                        g["groups"] = [{"group": "Sieger", "items": [item(a, p2), item(b, 1 - p2)]}]
            g["n_games"] = min(pm_.elo.games.get(a, 0), pm_.elo.games.get(b, 0))
        if not g["model"] and not g["market"]:
            continue
        g["model"] = {k: round(float(v), 4) for k, v in g["model"].items()}
        # 2) Polymarket-Märkte dieses Spiels mit Preis und EV (aus den Prognosen)
        if not edges.empty:
            day = pd.Timestamp(g["start"], unit="s").strftime("%Y-%m-%d")
            sub = edges[(edges["code"] == g["code"]) & (pd.to_datetime(edges["game_start"], unit="s").dt.strftime("%Y-%m-%d") == day)]
            if not sub.empty:
                same = sub["event_title"].map(lambda t: _same_match(t, a, b)).astype(bool)
                sub = sub[same & ~sub["suspicious"].astype(bool)].sort_values("ev", ascending=False)
            g["polymarket"] = [
                {"pick": r.pick, "market": r.market_type, "p": round(float(r.prob), 4), "price": round(float(r.price), 3),
                 "ev": round(float(r.ev), 4), "liq": 0 if pd.isna(r.liquidity) else round(float(r.liquidity)), "news": bool(r.news_alert)}
                for r in sub.head(12).itertuples()
            ]
        else:
            g["polymarket"] = []
        # 3) Quoten von Tipico, Betano, Winamax
        g["books"] = bookmakers.for_game(a, b, g["start"], book_odds, fam) if book_odds is not None and not book_odds.empty else None
        # 4) NBA-Spielerwetten
        g["props"] = []
        if with_props and g["code"] == "nba":
            from . import props

            if pm_props is None:
                try:
                    pm_props = props.polymarket_props(hours)
                except Exception:  # noqa: BLE001
                    pm_props = pd.DataFrame()
            g["props"] = compact_props(props.game_props(a, b, pm_props))
        out.append(g)
    out.sort(key=lambda x: (x["start"], -x["liquidity"]))
    progress(f"{len(out)} Spiele aufbereitet")
    return out


def compact_props(rows: list[dict]) -> list[dict]:
    """Pro Spieler und Statistik: Ø-Linie und die Polymarket-Hauptlinie (Preis am nächsten an 50 %)."""
    out = []
    for r in rows:
        stats = []
        by_stat: dict[str, list[dict]] = {}
        for l in r["lines"]:
            by_stat.setdefault(l["stat"], []).append(l)
        for stat, ls in by_stat.items():
            avg = next((l for l in ls if l["src"] == "Ø-Linie"), None) or ls[0]
            liquid = [l for l in ls if l.get("pm_over") and (l.get("liq") or 0) >= 200 and l["pm_over"] + (l.get("pm_under") or 1) <= 1.1]
            main = min(liquid, key=lambda l: abs(l["pm_over"] - 0.5)) if liquid else None
            st = {"stat": stat, "label": avg["label"], "avg": avg["avg"], "exp": avg["exp"], "line": avg["line"], "p_over": avg["p_over"]}
            if main:
                st["pm"] = {"line": main["line"], "p_over": main["p_over"], "price_over": round(float(main["pm_over"]), 3),
                            "price_under": round(float(main["pm_under"]), 3), "ev_over": main.get("ev_over"), "ev_under": main.get("ev_under")}
            stats.append(st)
        order = ["pts", "reb", "ast", "fg3", "pra"]
        stats.sort(key=lambda s: order.index(s["stat"]) if s["stat"] in order else 9)
        out.append({"player": r["player"], "team": r["team"], "side": r["side"], "min": r["min"], "status": r["status"], "stats": stats})
    return out


def _same_match(title: str | None, a: str, b: str) -> bool:
    s = parse_sides(title or "")
    return bool(s) and {_norm(s[0]), _norm(s[1])} == {_norm(a), _norm(b)}


def favorite_text(g: dict) -> str:
    """Kurzer Satz: Wer ist Favorit und wie sicher?"""
    m = g.get("model") or g.get("market") or {}
    if not m:
        return ""
    k = max((x for x in ("a", "b") if x in m), key=lambda x: m[x])
    name = g[k]
    p = m[k]
    word = "klarer Favorit" if p >= 0.7 else "Favorit" if p >= 0.55 else "knapp vorne"
    return f"{name} {word} ({p:.0%})"



PREFERRED_BOOKS = ["Tipico", "Betano", "Winamax"]


def book_list(g: dict) -> list[str]:
    """Anbieter, die für dieses Spiel Quoten haben: zuerst Tipico, Betano, Winamax, dann weitere deutsche Anbieter."""
    bk = g.get("books") or {}
    names = set(bk.get("h2h", {})) | {b for line in bk.get("totals", {}).values() for b in line}
    return [b for b in PREFERRED_BOOKS if b in names or not names] + sorted(names - set(PREFERRED_BOOKS))


def books_consensus(g: dict) -> dict | None:
    """Wahrscheinlichkeiten laut Buchmachern (Durchschnitt, Marge herausgerechnet)."""
    h2h = (g.get("books") or {}).get("h2h") or {}
    probs = []
    for o in h2h.values():
        inv = {k: 1 / v for k, v in o.items() if v}
        t = sum(inv.values())
        if t and "a" in inv and "b" in inv:
            probs.append({k: x / t for k, x in inv.items()})
    if not probs:
        return None
    keys = set.intersection(*(set(p) for p in probs))
    return {k: sum(p[k] for p in probs) / len(probs) for k in keys}


def books_rows(g: dict) -> list[dict]:
    """Vergleichstabelle für ein Spiel: je Tipp Modell-Wahrscheinlichkeit, faire Quote und die Quoten von
    Tipico, Betano, Winamax; dazu der Anbieter mit der besten Quote und deren Value laut Modell."""
    bk = g.get("books") or {}
    if not bk:
        return []
    model = dict(g.get("model") or {})
    labels = {it["label"]: it["p"] for grp in g.get("groups") or [] for it in grp["items"]}
    # Anbieter-Quoten mit Remis bei Eishockey & Co. gelten für die reguläre Spielzeit -> 3-Weg-Markt vergleichen
    has_draw = any("draw" in o for o in bk.get("h2h", {}).values())
    if has_draw and "draw" not in model:
        three = next((grp["items"] for grp in g.get("groups") or [] if grp["group"].startswith("3-Weg")), None)
        model = {"a": three[0]["p"], "draw": three[1]["p"], "b": three[2]["p"]} if three and len(three) == 3 else {}
    cons = books_consensus(g) or {}
    rows = []
    for key, label in (("a", f"Sieg {g['a']}"), ("draw", "Unentschieden"), ("b", f"Sieg {g['b']}")):
        offers = {book: o.get(key) for book, o in bk.get("h2h", {}).items() if o.get(key)}
        if not offers:
            continue
        rows.append(_book_row(label, model.get(key), offers, cons.get(key)))
    for line, books in sorted(bk.get("totals", {}).items(), key=lambda kv: float(kv[0])):
        q_over = _pair_consensus(books)
        for side, name in (("over", "Über"), ("under", "Unter")):
            offers = {book: o.get(side) for book, o in books.items() if o.get(side)}
            if offers:
                lab = f"{name} {line.replace('.', ',')}"
                q = None if q_over is None else (q_over if side == "over" else 1 - q_over)
                rows.append(_book_row(lab, labels.get(lab), offers, q))
    return rows


def _pair_consensus(books: dict) -> float | None:
    """Faire Wahrscheinlichkeit für „über“ laut Anbietern (Marge herausgerechnet, Durchschnitt)."""
    qs = [(1 / o["over"]) / (1 / o["over"] + 1 / o["under"]) for o in books.values() if o.get("over") and o.get("under")]
    return sum(qs) / len(qs) if qs else None


# Ab wann Modell und Anbieter „zu weit auseinander“ liegen. Im Backtest sind die Anbieter meist genauer als das
# Modell; echte Vorteile liegen bei wenigen Prozentpunkten. Größere Abstände heißen fast immer: dem Modell fehlt
# eine Information (Aufstellung, Rotation, Motivation, Wechsel), kein Geschenk des Anbieters.
MARKET_GAP = 0.10     # mehr als 10 Prozentpunkte Unterschied
MARKET_RATIO = 1.3    # oder eine Seite mehr als 1,3-mal so wahrscheinlich wie laut Anbietern


def disagrees_with_market(p: float, q: float) -> bool:
    if abs(p - q) > MARKET_GAP:
        return True
    return max(p / max(q, 1e-6), (1 - p) / max(1 - q, 1e-6)) > MARKET_RATIO


def _book_row(label: str, p: float | None, offers: dict[str, float], q: float | None = None) -> dict:
    """q = faire Wahrscheinlichkeit laut allen Anbietern zusammen (ohne Marge)."""
    book, value = bookmakers.best_value(p, offers)
    if not (p and book):
        unsure = False
    elif q is not None:
        unsure = disagrees_with_market(p, q)
    else:
        q1 = 1 / offers[book] / 1.05  # keine Gegenseite bekannt: rund 5 % Marge herausrechnen
        unsure = disagrees_with_market(p, q1)
    row = {"tipp": label, "p": p, "fair": odds(p) if p else None, "best": book, "value": value, "unsure": unsure}
    row.update(offers)
    return row
