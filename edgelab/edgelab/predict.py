"""Modell-Wahrscheinlichkeiten für offene Märkte berechnen und mit Marktpreisen vergleichen."""
from __future__ import annotations

import logging
import re

import pandas as pd

from . import betting, config, darts, db, injuries
from . import polymarket as pmapi
from .families import excluded, is_neutral, parse_sides
from .modeling import PoolModels, load_models

log = logging.getLogger(__name__)

_GAME_TOTAL = re.compile(r":\s*O/U\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)
_SPREAD = re.compile(r"^Spread:\s*(.+?)\s*\(([-+]?\d+(?:\.\d+)?)\)\s*$", re.IGNORECASE)
_TEAM_TOTAL = re.compile(r"^(?:.*?:\s*)?(.+?)\s+(?:Team Total:\s*)?O/U\s*(\d+(?:\.\d+)?)\s*$", re.IGNORECASE)


def _norm(s: str | None) -> str:
    return " ".join((s or "").lower().replace(".", "").split())


def _side_of(name: str, a: str, b: str) -> str | None:
    n = _norm(name)
    if n == _norm(a):
        return "a"
    if n == _norm(b):
        return "b"
    return None


def _home(family: str, ordering: str | None) -> str | None:
    if is_neutral(family) or ordering not in ("home", "away"):
        return None
    return "a" if ordering == "home" else "b"


def sides_of(m: dict) -> tuple[str, str] | None:
    if m["market_type"] == "moneyline" and _norm(m["outcome0"]) not in ("yes", "no"):
        return m["outcome0"], m["outcome1"]
    return parse_sides(m["event_title"] or "")


def market_probs(
    m: dict, pm: PoolModels, ordering: str | None, adj: tuple[float, float] = (0.0, 0.0)
) -> tuple[float | None, str]:
    """P(outcome0) für einen Markt, oder (None, '') wenn das Modell ihn nicht bepreisen kann.
    adj = Punkte, die Seite A/B durch Verletzungen verlieren (nur Punktemodell/Elo)."""
    family = pm.family
    home = _home(family, ordering)
    mt = m["market_type"]
    o0 = m["outcome0"] or ""

    if mt == "moneyline" and _norm(o0) not in ("yes", "no"):
        a, b = m["outcome0"], m["outcome1"]
        if family == "darts":
            sa, sb = darts.stats(a), darts.stats(b)
            if sa and sb:
                return darts.match_prob(sa["avg"], sb["avg"], darts.first_to(m["event_title"])), "Average-Modell"
        return pm.win_prob(a, b, home, m["code"], adj)

    sides = parse_sides(m["event_title"] or "")
    if not sides:
        return None, ""
    a, b = sides
    g = pm.goals_for(a, b)
    pt = pm.points_for(m["code"], a, b)
    q = m["question"] or ""

    if mt == "moneyline":  # Ja/Nein: "Will A win?" / "... end in a draw?"
        if g is None:
            return None, ""
        pr = g.predict(a, b, home)
        gi = _norm(m["group_item"])
        if gi.startswith("draw") or "draw" in _norm(q):
            p = pr["draw"]
        else:
            side = _side_of(m["group_item"] or "", a, b)
            if side is None:
                return None, ""
            p = pr[side]
        return (p if _norm(o0) == "yes" else 1 - p), "Poisson"

    model = g or pt
    if model is None:
        return None, ""
    label = "Poisson" if g is not None else "Punkte"
    pr = model.predict(a, b, home) if g is not None else model.predict(a, b, home, adj[0], adj[1])
    line = m["line"]

    if mt == "totals":
        if line is None or not _GAME_TOTAL.search(q) or re.search(r"sets|rounds|games|maps|corners|half|cards", q, re.I):
            return None, ""
        p_over = pr["over"](line)
        return (p_over if _norm(o0) == "over" else 1 - p_over), label

    if mt == "spreads":
        mm = _SPREAD.match(q)
        if not mm or line is None:
            return None, ""
        side = _side_of(o0, a, b)
        if side is None:
            return None, ""
        return pr["cover"](side, float(line)), label

    if mt == "both_teams_to_score" and g is not None:
        p = g.predict(a, b, home)["btts"]
        return (p if _norm(o0) == "yes" else 1 - p), "Poisson"

    if mt in ("soccer_team_totals", "team_totals") and g is not None and line is not None:
        gi = m["group_item"] or ""
        team = re.sub(r"\s*O/U.*$", "", gi, flags=re.I).strip()
        side = _side_of(team, a, b)
        if side is None:
            return None, ""
        p = g.predict(a, b, home)["team_over"](side, float(line))
        return (p if _norm(o0) == "over" else 1 - p), "Poisson"

    return None, ""


def run(models: dict[str, PoolModels] | None = None, progress=lambda s: None) -> int:
    models = models if models is not None else load_models()
    now = db.now()
    with db.conn() as con:
        meta = {r["code"]: dict(r) for r in con.execute("SELECT code, pool, family, ordering FROM sports")}
        markets = [
            dict(r)
            for r in con.execute(
                "SELECT * FROM markets WHERE closed=0 AND is_sports=1 AND game_start > ? AND game_start < ?",
                (now - 600, now + (config.LOOKAHEAD_HOURS + 24) * 3600),
            )
        ]
    try:
        adjuster = injuries.Adjuster()
    except Exception as e:  # noqa: BLE001
        log.warning("Verletzungen nicht verfügbar: %s", e)
        adjuster = None
    rows = []
    for m in markets:
        sm = meta.get(m["code"])
        if not sm or excluded(sm["family"]):
            continue
        pm = models.get(sm["pool"])
        if pm is None:
            continue
        adj, note = (0.0, 0.0), ""
        sides = sides_of(m)
        if adjuster is not None and sides and m["code"] in injuries.LEAGUES:
            pa, pb, note = adjuster.game(m["code"], *sides)
            # Spread/Total: Seitennamen aus dem Titel; bei Zwei-Namen-Märkten aus den Outcomes
            adj = (pa, pb)
        try:
            p0, label = market_probs(m, pm, sm["ordering"], adj)
        except Exception as e:  # noqa: BLE001
            log.debug("predict %s: %s", m["condition_id"], e)
            continue
        if p0 is None or not (0.0 < p0 < 1.0):
            continue
        n = min(pm.elo.games.get(sides[0], 0), pm.elo.games.get(sides[1], 0)) if sides else 0
        for idx, p in ((0, p0), (1, 1 - p0)):
            rows.append(
                {"condition_id": m["condition_id"], "outcome_index": idx, "model": label, "prob": float(p),
                 "n_games": int(n), "injury_adj": adj[0] - adj[1] if idx == 0 else adj[1] - adj[0],
                 "injury_note": note or None, "updated": now}
            )
    with db.conn() as con:
        db.upsert(con, "predictions", rows, ("condition_id", "outcome_index"))
        # Alles, was in diesem Lauf nicht neu berechnet wurde, ist veraltet (z. B. ausgeschlossene Sportarten)
        con.execute("DELETE FROM predictions WHERE updated < ?", (now,))
    db.kv_set("last_predict", now)
    progress(f"{len(rows) // 2} Märkte bepreist")
    try:
        n_moves = update_moves()
        progress(f"Marktbewegung für {n_moves} Märkte geprüft")
    except Exception as e:  # noqa: BLE001
        log.warning("Marktbewegung: %s", e)
    return len(rows) // 2


MOVE_ALERT = 0.08  # Preisänderung ab 8 Prozentpunkten in 24 h = mögliche News (Verletzung, Aufstellung)


def update_moves(max_markets: int = 400) -> int:
    """Preisänderung der letzten 24 h für Märkte, bei denen Modell und Markt auseinanderliegen.
    Starke Bewegung deutet auf News hin, die das Modell nicht kennt."""
    now = db.now()
    cand = db.df(
        """
        SELECT DISTINCT m.condition_id, m.token0 FROM predictions p JOIN markets m USING(condition_id)
        WHERE p.outcome_index = 0 AND m.closed = 0 AND m.game_start > ? AND m.liquidity >= 300
          AND ABS(p.prob - COALESCE(m.best_ask, m.price0)) >= 0.03
        ORDER BY ABS(p.prob - COALESCE(m.best_ask, m.price0)) DESC LIMIT ?
        """,
        (now, max_markets),
    )
    if cand.empty:
        return 0

    def work(row) -> dict:
        try:
            d = (pmapi.get(f"{config.DATA_API}/prices-history", params={"token_id": row.token0, "start": now - 86400, "end": now}, retries=2) or {}).get("data") or []
        except Exception:  # noqa: BLE001
            d = []
        move = float(d[-1]["price"]) - float(d[0]["price"]) if len(d) >= 2 else None
        return {"condition_id": row.condition_id, "move0": move, "updated": now}

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=config.HTTP_WORKERS) as ex:
        rows = list(ex.map(work, cand.itertuples()))
    with db.conn() as con:
        db.upsert(con, "price_moves", rows, "condition_id")
    return len(rows)


def buy_price(row: pd.Series) -> float | None:
    """Preis, zu dem man das Outcome jetzt kaufen kann (Ask)."""
    if row["outcome_index"] == 0:
        p = row["best_ask"] if pd.notna(row["best_ask"]) and 0 < row["best_ask"] < 1 else row["price0"]
    else:
        p = 1 - row["best_bid"] if pd.notna(row["best_bid"]) and 0 < row["best_bid"] < 1 else row["price1"]
    return float(p) if pd.notna(p) else None


def edges(min_liquidity: float = 0.0, include_started: bool = False) -> pd.DataFrame:
    """Alle bepreisten Outcomes mit Marktpreis, EV und Kelly."""
    now = db.now()
    d = db.df(
        """
        SELECT p.condition_id, p.outcome_index, p.model, p.prob, p.n_games, p.injury_adj, p.injury_note,
               CASE WHEN p.outcome_index = 0 THEN mv.move0 ELSE -mv.move0 END AS move24, m.market_type, m.question, m.event_title,
               m.event_slug, m.group_item, m.outcome0, m.outcome1, m.line, m.game_start, m.best_bid, m.best_ask,
               m.price0, m.price1, m.liquidity, m.volume, m.fee_rate, m.code, m.token0, m.token1,
               s.name AS league, s.family
        FROM predictions p JOIN markets m USING(condition_id) JOIN sports s ON s.code = m.code
        LEFT JOIN price_moves mv ON mv.condition_id = p.condition_id
        WHERE m.closed = 0 AND m.game_start > ?
        """,
        (now - (6 * 3600 if include_started else 0),),
    )
    if d.empty:
        return d
    d = d[~d["family"].map(excluded)]
    if d.empty:
        return d
    d["outcome"] = d.apply(lambda r: r["outcome0"] if r["outcome_index"] == 0 else r["outcome1"], axis=1)
    d["price"] = d.apply(buy_price, axis=1)
    d = d[d["price"].notna() & (d["price"] > 0.01) & (d["price"] < 0.99)]
    d = d[d["liquidity"].fillna(0) >= min_liquidity]
    fee = d["fee_rate"].fillna(config.DEFAULT_FEE_RATE)
    d["edge"] = d["prob"] - d["price"]
    d["ev"] = [betting.ev(p, c, f) for p, c, f in zip(d["prob"], d["price"], fee)]
    d["kelly"] = [betting.kelly(p, c, f) for p, c, f in zip(d["prob"], d["price"], fee)]
    d["fair_odds"] = 1 / d["prob"]
    d["market_odds"] = 1 / d["price"]
    d["start"] = pd.to_datetime(d["game_start"], unit="s", utc=True).dt.tz_convert("Europe/Berlin")
    d["token_id"] = d.apply(lambda r: r["token0"] if r["outcome_index"] == 0 else r["token1"], axis=1)
    d["pick"] = d.apply(pick_label, axis=1)
    d["match"] = d["event_title"].map(lambda t: " vs. ".join(parse_sides(t) or ()) or t)
    # Sehr große Abweichung vom Markt ist meist ein Modellfehler (zu wenig Daten), kein Value
    d["suspicious"] = [is_suspicious(p, c) for p, c in zip(d["prob"], d["price"])]
    # Markt hat sich stark gegen unseren Tipp bewegt -> wahrscheinlich News, die das Modell nicht kennt
    d["news_alert"] = d["move24"].notna() & (d["move24"] <= -MOVE_ALERT)
    return d.sort_values("ev", ascending=False).reset_index(drop=True)


SUSPICIOUS_EDGE = 0.25
SUSPICIOUS_RATIO = 2.5


def is_suspicious(prob: float, price: float) -> bool:
    """Absolut > 25 Prozentpunkte daneben oder relativ > 2,5-fach (typisch bei Außenseitern)."""
    if abs(prob - price) > SUSPICIOUS_EDGE:
        return True
    return max(prob / max(price, 1e-6), (1 - prob) / max(1 - price, 1e-6)) > SUSPICIOUS_RATIO


def pick_label(r: pd.Series) -> str:
    out = r["outcome"]
    mt = r["market_type"]
    if _norm(out) in ("yes", "no"):
        q = r["group_item"] if isinstance(r["group_item"], str) and r["group_item"] else r["question"]
        if mt == "moneyline":
            q = "Unentschieden" if _norm(q).startswith("draw") else f"Sieg {q}"
        return f"{q}: {'Ja' if _norm(out) == 'yes' else 'Nein'}"
    if mt == "totals":
        return f"{'Über' if _norm(out) == 'over' else 'Unter'} {r['line']:g}".replace(".", ",")
    if mt in ("soccer_team_totals", "team_totals"):
        team = re.sub(r"\s*O/U.*$", "", str(r["group_item"] or ""), flags=re.I)
        return f"{team} {'über' if _norm(out) == 'over' else 'unter'} {r['line']:g} Tore".replace(f"{r['line']:g}", f"{r['line']:g}".replace(".", ","))
    if mt == "spreads" and pd.notna(r["line"]):
        line = r["line"] if r["outcome_index"] == 0 else -r["line"]
        return f"{out} {line:+g}".replace(f"{line:+g}", f"{line:+g}".replace(".", ","))
    return f"Sieg {out}"
