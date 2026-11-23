"""Positions-Tracker: eigene Wetten erfassen, automatisch abrechnen, ROI und CLV auswerten."""
from __future__ import annotations

import pandas as pd

from . import db, ingest


def add(
    description: str,
    odds: float,
    stake: float,
    family: str = "",
    bookmaker: str = "Polymarket",
    condition_id: str | None = None,
    outcome_index: int | None = None,
    model_prob: float | None = None,
    event_time: int | None = None,
    notes: str = "",
) -> int:
    with db.conn() as con:
        cur = con.execute(
            "INSERT INTO positions (created, event_time, description, family, condition_id, outcome_index, odds, stake, "
            "bookmaker, model_prob, status, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (db.now(), event_time, description, family, condition_id, outcome_index, odds, stake, bookmaker, model_prob, notes),
        )
        return int(cur.lastrowid)


def settle(position_id: int, status: str) -> None:
    with db.conn() as con:
        r = con.execute("SELECT odds, stake FROM positions WHERE id=?", (position_id,)).fetchone()
        if not r:
            return
        payout = {"won": r["stake"] * r["odds"], "void": r["stake"], "lost": 0.0}.get(status)
        con.execute(
            "UPDATE positions SET status=?, payout=?, settled_ts=? WHERE id=?",
            (status, payout, db.now() if status != "open" else None, position_id),
        )


def delete(position_id: int) -> None:
    with db.conn() as con:
        con.execute("DELETE FROM positions WHERE id=?", (position_id,))


def auto_settle() -> int:
    """Rechnet verknüpfte Positionen ab, sobald der Polymarket-Markt aufgelöst ist, und holt den Schlusskurs (CLV)."""
    ingest.refresh_stale()
    with db.conn() as con:
        open_pos = [
            dict(r)
            for r in con.execute(
                "SELECT p.id, p.outcome_index, p.closing_odds, m.closed, m.price0, m.price1, m.token0, m.token1, m.game_start "
                "FROM positions p JOIN markets m ON m.condition_id = p.condition_id "
                "WHERE p.condition_id IS NOT NULL AND (p.status = 'open' OR p.closing_odds IS NULL)"
            )
        ]
    n = 0
    now = db.now()
    need_close = [
        (p["token0"] if p["outcome_index"] == 0 else p["token1"], p["game_start"])
        for p in open_pos
        if p["closing_odds"] is None and p["game_start"] and p["game_start"] < now
    ]
    closes = ingest.closing_prices(need_close) if need_close else {}
    for p in open_pos:
        tok = p["token0"] if p["outcome_index"] == 0 else p["token1"]
        cp = closes.get(tok)
        if cp:
            with db.conn() as con:
                con.execute("UPDATE positions SET closing_odds=? WHERE id=?", (1 / cp, p["id"]))
        if p["closed"]:
            res = p["price0"] if p["outcome_index"] == 0 else p["price1"]
            status = "won" if res == 1 else "lost" if res == 0 else "void" if res == 0.5 else None
            if status:
                settle(p["id"], status)
                n += 1
    return n


def table() -> pd.DataFrame:
    d = db.df("SELECT * FROM positions ORDER BY created DESC")
    if d.empty:
        return d
    d["profit"] = d.apply(lambda r: (r["payout"] or 0) - r["stake"] if r["status"] != "open" else None, axis=1)
    d["clv"] = d["odds"] / d["closing_odds"] - 1
    d["ev_at_bet"] = d["model_prob"] * d["odds"] - 1
    d["created_dt"] = pd.to_datetime(d["created"], unit="s", utc=True).dt.tz_convert("Europe/Berlin")
    return d


def stats(d: pd.DataFrame) -> dict:
    s = d[d["status"].isin(["won", "lost", "void"])]
    staked = float(s["stake"].sum())
    profit = float(s["profit"].sum())
    decided = s[s["status"].isin(["won", "lost"])]
    return {
        "staked": staked,
        "profit": profit,
        "roi": profit / staked if staked else 0.0,
        "hit_rate": float((decided["status"] == "won").mean()) if len(decided) else 0.0,
        "open": int((d["status"] == "open").sum()),
        "settled": int(len(s)),
        "avg_clv": float(d["clv"].dropna().mean()) if d["clv"].notna().any() else None,
    }
