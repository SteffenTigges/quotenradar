"""Whale Tracker: große Sportwetten auf Polymarket in Echtzeit erkennen und Wallets einordnen."""
from __future__ import annotations

import logging

import pandas as pd

from . import config, db, fx, ingest, notify
from .families import excluded, odds_link
from . import polymarket as pm

log = logging.getLogger(__name__)

LABELS = ["Sharp", "Neutral", "Frische Wallet", "Dumb Money"]


def classify_wallet(
    join_ts: int | None, trades: int | None, sports_pnl: float | None, sports_volume: float | None, now: int
) -> str:
    """Sharp = nachweislich profitabel im Sport, Dumb Money = deutlich im Minus,
    Frische Wallet = neu oder kaum Historie."""
    pnl = sports_pnl or 0.0
    vol = sports_volume or 0.0
    if pnl >= 20_000 and vol > 0 and pnl / vol >= 0.01:
        return "Sharp"
    if pnl <= -10_000:
        return "Dumb Money"
    age_days = (now - join_ts) / 86400 if join_ts else None
    if (age_days is None or age_days < 30) or (trades is not None and trades < 25):
        return "Frische Wallet"
    return "Neutral"


def trade_pnl(side: str, size: float, price: float, resolved: float | None) -> float | None:
    """Gewinn eines Trades, wenn bis zur Auflösung gehalten (resolved = 1, 0 oder 0.5)."""
    if resolved is None:
        return None
    if side == "BUY":
        return size * (resolved - price)
    return size * (price - resolved)


def _known_conditions(ids: list[str]) -> dict[str, dict]:
    if not ids:
        return {}
    out = {}
    with db.conn() as con:
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            for r in con.execute(
                f"SELECT condition_id, is_sports, code FROM markets WHERE condition_id IN ({','.join('?' * len(chunk))})",
                chunk,
            ):
                out[r["condition_id"]] = dict(r)
    return out


def _mark_non_sports(ids: list[str]) -> None:
    rows = [{"condition_id": c, "is_sports": 0, "updated": db.now()} for c in ids]
    with db.conn() as con:
        db.upsert(con, "markets", rows, "condition_id")


def enrich_wallets(addresses: list[str], force: bool = False) -> None:
    now = db.now()
    with db.conn() as con:
        fresh = {
            r["address"]
            for r in con.execute(
                f"SELECT address FROM wallets WHERE updated > ? AND address IN ({','.join('?' * len(addresses))})",
                [now - config.WALLET_CACHE_HOURS * 3600, *addresses],
            )
        } if addresses and not force else set()
    rows = []
    for a in dict.fromkeys(addresses):
        if a in fresh:
            continue
        try:
            st = pm.user_stats(a)
            lb = pm.leaderboard_user(a, "sports", "all")
        except Exception as e:  # noqa: BLE001
            log.debug("wallet %s: %s", a, e)
            continue
        allp = st.get("all_time_pnl") or {}
        row = {
            "address": a,
            "name": lb.get("user_name") or "",
            "join_ts": st.get("join_date"),
            "trades": st.get("trades"),
            "all_pnl": allp.get("economic_pnl", allp.get("realized_pnl")),
            "all_volume": allp.get("volume"),
            "sports_pnl": lb.get("pnl"),
            "sports_volume": lb.get("volume"),
            "sports_rank": lb.get("rank_pnl"),
            "updated": now,
        }
        row["label"] = classify_wallet(row["join_ts"], row["trades"], row["sports_pnl"], row["sports_volume"], now)
        rows.append(row)
    with db.conn() as con:
        db.upsert(con, "wallets", rows, "address")


def poll(min_usd: float | None = None, max_pages: int = 6, progress=lambda s: None) -> int:
    """Holt neue große Trades, behält nur Sport, ordnet Wallets ein, schickt ggf. Benachrichtigungen."""
    min_usd = min_usd or config.WHALE_MIN_USD
    with db.conn() as con:
        has_leagues = con.execute("SELECT COUNT(*) FROM series_map").fetchone()[0] > 0
    if not has_leagues:  # ohne Ligen-Tabelle würde jeder Markt als „kein Sport“ markiert
        ingest.sync_sports()
    last_ts = db.kv_get("whale_last_ts", db.now() - 3 * 3600)
    new: list[dict] = []
    cursor = None
    newest = last_ts
    for _ in range(max_pages):
        page = pm.trades(min_usd, limit=500, cursor=cursor)
        data = page.get("data") or []
        stop = False
        for t in data:
            ts = int(t.get("timestamp") or 0)
            if ts <= last_ts:
                stop = True
                break
            newest = max(newest, ts)
            new.append(t)
        cursor = (page.get("pagination") or {}).get("next_cursor")
        if stop or not cursor or not data:
            break
    if not new:
        return 0

    conds = list({t["condition_id"] for t in new if t.get("condition_id")})
    known = _known_conditions(conds)
    unknown = [c for c in conds if c not in known]
    if unknown:
        rows = ingest.save_markets(pm.markets_by_condition(unknown))
        found = {r["condition_id"] for r in rows}
        _mark_non_sports([c for c in unknown if c not in found])
        known = _known_conditions(conds)

    with db.conn() as con:
        preds = {
            (r["condition_id"], r["outcome_index"]): r["prob"]
            for r in con.execute(
                f"SELECT condition_id, outcome_index, prob FROM predictions WHERE condition_id IN ({','.join('?' * len(conds))})",
                conds,
            )
        }
    with db.conn() as con:
        fam_of = {r["code"]: r["family"] for r in con.execute("SELECT code, family FROM sports")}
    rows = []
    for t in new:
        k = known.get(t.get("condition_id"))
        if not k or not k.get("is_sports") or excluded(fam_of.get(k.get("code"))):
            continue
        size, price = float(t.get("size") or 0), float(t.get("price") or 0)
        oi = t.get("outcome_index")
        rows.append(
            {
                "id": f"{t.get('transaction_hash')}:{t.get('token_id')}:{t.get('proxy_wallet')}",
                "ts": int(t.get("timestamp") or 0),
                "wallet": t.get("proxy_wallet"),
                "condition_id": t.get("condition_id"),
                "token_id": t.get("token_id"),
                "side": t.get("side"),
                "outcome": t.get("outcome"),
                "outcome_index": oi,
                "size": size,
                "price": price,
                "usd": size * price,
                "title": t.get("title"),
                "event_slug": t.get("event_slug"),
                "code": k.get("code"),
                "model_prob": preds.get((t.get("condition_id"), oi)),
            }
        )
    with db.conn() as con:
        con.executemany(
            "INSERT OR IGNORE INTO whale_trades (id, ts, wallet, condition_id, token_id, side, outcome, outcome_index, "
            "size, price, usd, title, event_slug, code, model_prob) VALUES "
            "(:id, :ts, :wallet, :condition_id, :token_id, :side, :outcome, :outcome_index, :size, :price, :usd, "
            ":title, :event_slug, :code, :model_prob)",
            rows,
        )
    db.kv_set("whale_last_ts", newest)
    enrich_wallets([r["wallet"] for r in rows])
    _notify_new()
    db.kv_set("last_whale_poll", db.now())
    progress(f"Whales: {len(rows)} neue Sport-Trades (von {len(new)} großen Trades)")
    return len(rows)


def _notify_new() -> None:
    if not notify.enabled():
        return
    d = db.df(
        "SELECT t.*, w.label, w.sports_pnl, w.name FROM whale_trades t LEFT JOIN wallets w ON w.address = t.wallet "
        "WHERE t.notified = 0 AND t.usd >= ? AND t.side = 'BUY' AND w.label = 'Sharp'",
        (config.WHALE_NOTIFY_USD,),
    )
    for _, r in d.iterrows():
        model = ""
        if pd.notna(r["model_prob"]):
            diff = r["model_prob"] - r["price"]
            model = f"\nModell: {fx.prozent(r['model_prob'], 0)} ({'stimmt zu' if diff > 0 else 'widerspricht'}, {fx.prozent(diff, vorzeichen=True)})"
        msg = (
            f"🐋 Profi-Wette: {fx.euro(fx.to_eur(r['usd']))}\n{r['title']}\n{r['outcome']} – Quote {fx.quote(1 / r['price'])}"
            f"\nKonto: {r['name'] or r['wallet'][:10]} (Sportgewinn {fx.euro(fx.to_eur(r['sports_pnl'] or 0))}){model}"
            f"\nQuoten vergleichen: {odds_link(r['title'], None)}"
        )
        notify.send(msg)
    if not d.empty:
        with db.conn() as con:
            con.executemany("UPDATE whale_trades SET notified=1 WHERE id=?", [(i,) for i in d["id"]])


def feed(hours: int = 24) -> pd.DataFrame:
    d = db.df(
        """
        SELECT t.*, w.label, w.name, w.sports_pnl, w.sports_volume, w.sports_rank, w.join_ts,
               s.name AS league, s.family, m.closed, m.price0, m.price1, m.game_start, m.market_type, m.question
        FROM whale_trades t
        LEFT JOIN wallets w ON w.address = t.wallet
        LEFT JOIN markets m ON m.condition_id = t.condition_id
        LEFT JOIN sports s ON s.code = t.code
        WHERE t.ts > ? ORDER BY t.ts DESC
        """,
        (db.now() - hours * 3600,),
    )
    if d.empty:
        return d
    d = d[~d["family"].map(excluded)]
    if d.empty:
        return d
    d["label"] = d["label"].fillna("Unbekannt")
    d["time"] = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert("Europe/Berlin")
    rate = fx.eur_usd()
    d["eur"] = d["usd"] / rate
    d["pnl_eur"] = d["sports_pnl"] / rate
    d["model_edge"] = d["model_prob"] - d["price"]
    return d


def resolved_trades() -> pd.DataFrame:
    """Alle getrackten Trades mit Ergebnis (für Wallet-Ranglisten pro Sportart)."""
    d = db.df(
        """
        SELECT t.wallet, t.side, t.size, t.price, t.usd, t.outcome_index, t.ts, s.family, w.name, w.label,
               m.price0, m.price1
        FROM whale_trades t
        JOIN markets m ON m.condition_id = t.condition_id
        LEFT JOIN sports s ON s.code = t.code
        LEFT JOIN wallets w ON w.address = t.wallet
        WHERE m.closed = 1
        """
    )
    if d.empty:
        return d
    d = d[~d["family"].map(excluded)]
    if d.empty:
        return d
    d["resolved"] = [p0 if oi == 0 else p1 for oi, p0, p1 in zip(d["outcome_index"], d["price0"], d["price1"])]
    d["pnl"] = [trade_pnl(s, z, p, r) for s, z, p, r in zip(d["side"], d["size"], d["price"], d["resolved"])]
    return d


def leaderboard_by_family(min_trades: int = 3) -> pd.DataFrame:
    d = resolved_trades()
    if d.empty:
        return d
    g = d.groupby(["family", "wallet"]).agg(
        name=("name", "first"),
        label=("label", "first"),
        trades=("pnl", "size"),
        einsatz=("usd", "sum"),
        pnl=("pnl", "sum"),
        trefferquote=("pnl", lambda s: float((s > 0).mean())),
    )
    g = g[g["trades"] >= min_trades].reset_index()
    g["roi"] = g["pnl"] / g["einsatz"]
    rate = fx.eur_usd()
    g["einsatz"] = g["einsatz"] / rate
    g["pnl"] = g["pnl"] / rate
    return g.sort_values(["family", "pnl"], ascending=[True, False])
