"""Daten laden: Ligen, historische Ergebnisse, offene Märkte, Schlusskurse."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from . import config, db
from . import polymarket as pm
from .families import SCORE_FAMILIES, classify, is_neutral, parse_score, parse_sides

log = logging.getLogger(__name__)

# Markttypen, die unsere Modelle bepreisen können
FUTURE_END_DAYS = 8
MODEL_TYPES = ["moneyline", "spreads", "totals", "both_teams_to_score", "soccer_team_totals", "team_totals"]

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    pass


# ---------------------------------------------------------------- Ligen


def sync_sports() -> int:
    data = pm.sports()
    sports, smap = [], []
    for s in data:
        code = s.get("sport")
        if not code:
            continue
        family, pool = classify(code, s.get("name", ""), s.get("tags", ""))
        sports.append(
            {
                "code": code,
                "name": s.get("name"),
                "family": family,
                "pool": pool,
                "ordering": s.get("ordering"),
                "tag_ids": s.get("tags"),
                "image": s.get("image"),
            }
        )
        for sid in str(s.get("series") or "").split(","):
            if sid.strip():
                smap.append({"series_id": sid.strip(), "code": code})
    with db.conn() as con:
        db.upsert(con, "sports", sports, "code")
        db.upsert(con, "series_map", smap, "series_id")
    return len(sports)


def _series_codes() -> dict[str, str]:
    with db.conn() as con:
        return {r["series_id"]: r["code"] for r in con.execute("SELECT series_id, code FROM series_map")}


def _sports_meta() -> dict[str, dict]:
    with db.conn() as con:
        return {r["code"]: dict(r) for r in con.execute("SELECT * FROM sports")}


# ---------------------------------------------------------------- Märkte


def market_row(m: dict, series_codes: dict[str, str]) -> dict[str, Any] | None:
    ev = (m.get("events") or [{}])[0]
    code = None
    for s in ev.get("series") or []:
        code = series_codes.get(str(s.get("id")))
        if code:
            break
    outcomes = pm.jlist(m.get("outcomes"))
    tokens = pm.jlist(m.get("clobTokenIds"))
    prices = pm.jlist(m.get("outcomePrices"))
    if len(outcomes) != 2 or len(tokens) != 2:
        return None
    fee = None
    fs = m.get("feeSchedule")
    if isinstance(fs, dict) and m.get("feesEnabled"):
        fee = pm.fnum(fs.get("rate"))
    elif m.get("feesEnabled") is False:
        fee = 0.0
    return {
        "condition_id": m.get("conditionId"),
        "event_id": str(ev.get("id")) if ev.get("id") is not None else None,
        "code": code,
        "is_sports": 1 if code else 0,
        "market_type": m.get("sportsMarketType"),
        "question": m.get("question"),
        "event_title": ev.get("title"),
        "event_slug": ev.get("slug"),
        "group_item": m.get("groupItemTitle"),
        "outcome0": outcomes[0],
        "outcome1": outcomes[1],
        "token0": tokens[0],
        "token1": tokens[1],
        "line": pm.fnum(m.get("line")),
        "game_start": pm.parse_ts(m.get("gameStartTime")) or pm.parse_ts(ev.get("startTime")),
        "closed": 1 if m.get("closed") else 0,
        "price0": pm.fnum(prices[0]) if prices else None,
        "price1": pm.fnum(prices[1]) if len(prices) > 1 else None,
        "best_bid": pm.fnum(m.get("bestBid")),
        "best_ask": pm.fnum(m.get("bestAsk")),
        "liquidity": pm.fnum(m.get("liquidityNum") or m.get("liquidity")),
        "volume": pm.fnum(m.get("volumeNum") or m.get("volume")),
        "fee_rate": fee,
        "score": ev.get("score"),
        "updated": db.now(),
    }


def save_markets(raw: list[dict]) -> list[dict]:
    codes = _series_codes()
    rows = [r for r in (market_row(m, codes) for m in raw) if r and r["condition_id"]]
    with db.conn() as con:
        db.upsert(con, "markets", rows, "condition_id")
    return rows


# ---------------------------------------------------------------- Ergebnisse -> games


def _norm(s: str) -> str:
    return " ".join((s or "").lower().replace(".", "").split())


def build_games(market_rows: list[dict]) -> int:
    """Baut aus aufgelösten Moneyline-Märkten Spiele mit Ergebnis (pro Event ein Eintrag)."""
    meta = _sports_meta()
    event_ids = sorted({r["event_id"] for r in market_rows if r["market_type"] == "moneyline" and r["closed"] and r["event_id"] and r["code"]})
    by_event: dict[str, list[dict]] = {}
    with db.conn() as con:
        for i in range(0, len(event_ids), 500):
            chunk = event_ids[i : i + 500]
            for r in con.execute(
                f"SELECT * FROM markets WHERE market_type='moneyline' AND closed=1 AND event_id IN ({','.join('?' * len(chunk))})",
                chunk,
            ):
                by_event.setdefault(r["event_id"], []).append(dict(r))

    games = []
    for event_id, rows in by_event.items():
        first = rows[0]
        sm = meta.get(first["code"], {})
        family, pool = sm.get("family", "other"), sm.get("pool", "other")
        title = first["event_title"] or ""
        sides = parse_sides(title)
        score = parse_score(first["score"]) if family in SCORE_FAMILIES else None
        yes_no = _norm(first["outcome0"]) == "yes"
        g: dict[str, Any] = {
            "event_id": event_id,
            "code": first["code"],
            "pool": pool,
            "family": family,
            "title": title,
            "start_ts": first["game_start"],
            "score_raw": first["score"],
            "token_draw": None,
        }
        if not yes_no:
            # Zwei-Namen-Markt (Tennis, NBA, Esports, UFC …)
            ml = rows[0]
            a, b = ml["outcome0"], ml["outcome1"]
            if ml["price0"] == 1:
                result = "a"
            elif ml["price1"] == 1:
                result = "b"
            else:
                continue  # abgesagt / 50-50
            if score and sides and _norm(sides[0]) == _norm(b) and _norm(sides[1]) == _norm(a):
                score = (score[1], score[0])
            elif score and not (sides and _norm(sides[0]) == _norm(a)):
                score = None
            g.update(side_a=a, side_b=b, result=result, shape="h2h", token_a=ml["token0"], token_b=ml["token1"])
        else:
            # Ja/Nein pro Team + Unentschieden (Fußball u. a.)
            if not sides:
                continue
            a, b = sides
            ta = tb = td = None
            ra = rb = rd = None
            for r in rows:
                gi = _norm(r["group_item"] or "")
                if gi.startswith("draw") or "draw" in _norm(r["question"] or ""):
                    td, rd = r["token0"], r["price0"]
                elif gi == _norm(a):
                    ta, ra = r["token0"], r["price0"]
                elif gi == _norm(b):
                    tb, rb = r["token0"], r["price0"]
            if score:
                result = "a" if score[0] > score[1] else "b" if score[1] > score[0] else "draw"
            elif ra == 1:
                result = "a"
            elif rb == 1:
                result = "b"
            elif rd == 1:
                result = "draw"
            else:
                continue
            g.update(side_a=a, side_b=b, result=result, shape="3way", token_a=ta, token_b=tb, token_draw=td)
        if is_neutral(family) or sm.get("ordering") not in ("home", "away"):
            g["home"] = None
        else:
            g["home"] = "a" if sm["ordering"] == "home" else "b"
        g["score_a"], g["score_b"] = score if score else (None, None)
        if g["start_ts"] is None:
            continue
        games.append(g)
    with db.conn() as con:
        db.upsert(con, "games", games, "event_id")
    return len(games)


def _day_markets(day_start: int) -> list[dict]:
    return pm.markets_in_window(
        [("closed", "true"), ("tag_id", 1), ("sports_market_types", "moneyline")], day_start, day_start + 86400
    )


def backfill(days: int | None = None, progress: Progress = _noop) -> int:
    """Lädt aufgelöste Spiele der letzten `days` Tage. Bereits geladene Tage werden übersprungen
    (außer den letzten zwei, weil Ergebnisse nachträglich eintreffen)."""
    days = days or config.BACKFILL_DAYS
    today = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    done = set(db.kv_get("backfill_days", []))
    # Einzelsport-Märkte (Tennis, Darts, Esports …) haben als Enddatum Anpfiff + 7 Tage,
    # daher auch Tage in der Zukunft abfragen, sonst fehlen die Ergebnisse der letzten Woche.
    todo = [today - i * 86400 for i in range(days, -FUTURE_END_DAYS - 1, -1)]
    todo = [d for d in todo if d not in done or d >= today - 2 * 86400]
    total = 0

    def work(day: int) -> tuple[int, int]:
        raw = _day_markets(day)
        rows = save_markets(raw)
        return day, build_games(rows)

    with ThreadPoolExecutor(max_workers=config.HTTP_WORKERS) as ex:
        for i, (day, n) in enumerate(ex.map(work, todo), 1):
            total += n
            if day < today - 2 * 86400:
                done.add(day)
            if i % 5 == 0 or i == len(todo):
                db.kv_set("backfill_days", sorted(done))
                progress(f"Ergebnisse: {i}/{len(todo)} Tage geladen, {total} Spiele")
    db.kv_set("backfill_days", sorted(done))
    db.kv_set("last_results_update", db.now())
    return total


# ---------------------------------------------------------------- offene Märkte


def refresh_open(hours: int | None = None, progress: Progress = _noop) -> int:
    """Lädt alle bepreisbaren Märkte für Spiele in den nächsten `hours` Stunden."""
    hours = hours or config.LOOKAHEAD_HOURS
    now = db.now()
    params = [("closed", "false"), ("tag_id", 1)] + [("sports_market_types", t) for t in MODEL_TYPES]
    end = now + hours * 3600 + (FUTURE_END_DAYS + 1) * 86400
    windows = [(s, s + 6 * 3600) for s in range(now - 6 * 3600, end, 6 * 3600)]
    raw: list[dict] = []
    with ThreadPoolExecutor(max_workers=config.HTTP_WORKERS) as ex:
        for i, chunk in enumerate(ex.map(lambda w: pm.markets_in_window(params, *w), windows), 1):
            raw += chunk
            progress(f"Offene Märkte: Fenster {i}/{len(windows)}, {len(raw)} Märkte")
    horizon = now + hours * 3600
    raw = [m for m in raw if (pm.parse_ts(m.get("gameStartTime")) or 0) and pm.parse_ts(m.get("gameStartTime")) <= horizon]
    rows = save_markets(raw)
    db.kv_set("last_open_update", db.now())
    return len(rows)


def refresh_stale(max_markets: int = 2000) -> int:
    """Aktualisiert Märkte, deren Spiel vorbei ist, aber die bei uns noch als offen gelten
    (wichtig für Positionen und Whale-Auswertung)."""
    cutoff = db.now() - 3 * 3600
    with db.conn() as con:
        ids = [
            r["condition_id"]
            for r in con.execute(
                "SELECT condition_id FROM markets WHERE closed=0 AND is_sports=1 AND game_start < ? "
                "ORDER BY game_start DESC LIMIT ?",
                (cutoff, max_markets),
            )
        ]
    if not ids:
        return 0
    raw = pm.markets_by_condition(ids)
    rows = save_markets(raw)
    build_games(rows)
    return len(rows)


# ---------------------------------------------------------------- Schlusskurse


def closing_prices(tokens_with_ts: list[tuple[str, int]], progress: Progress = _noop) -> dict[str, float]:
    """Preis jedes Tokens zum Anpfiff (Polymarket-'Schlusskurs'). Ergebnisse werden gecacht."""
    with db.conn() as con:
        have = {
            r["token_id"]: r["price"]
            for r in con.execute(
                f"SELECT token_id, price FROM closing_prices WHERE token_id IN ({','.join('?' * len(tokens_with_ts))})",
                [t for t, _ in tokens_with_ts],
            )
        } if tokens_with_ts else {}
    todo = [(t, ts) for t, ts in tokens_with_ts if t and t not in have]

    def work(item: tuple[str, int]) -> dict:
        tok, ts = item
        try:
            p = pm.price_as_of(tok, ts - 60)
        except Exception as e:  # noqa: BLE001
            log.debug("closing price %s: %s", tok, e)
            p = None
        return {"token_id": tok, "ts": ts, "price": p}

    rows = []
    with ThreadPoolExecutor(max_workers=config.HTTP_WORKERS) as ex:
        for i, r in enumerate(ex.map(work, todo), 1):
            rows.append(r)
            if i % 50 == 0:
                progress(f"Schlusskurse: {i}/{len(todo)}")
    with db.conn() as con:
        db.upsert(con, "closing_prices", rows, "token_id")
    have.update({r["token_id"]: r["price"] for r in rows})
    return have


def setup(progress: Progress = _noop) -> None:
    db.init()
    t = time.time()
    n = sync_sports()
    progress(f"{n} Ligen geladen ({time.time() - t:.0f}s)")
