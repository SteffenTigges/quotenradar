"""SQLite-Datenbank: eine einzige Datei unter data/edgelab.sqlite."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Iterator

import pandas as pd

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sports (
    code TEXT PRIMARY KEY,        -- Polymarket-Kürzel, z. B. nba, bun, cs2
    name TEXT,
    family TEXT,                  -- soccer, basketball, tennis, esports, ...
    pool TEXT,                    -- Rating-Pool (Teams/Spieler, die gegeneinander antreten)
    ordering TEXT,                -- 'home' = erstes Team im Titel ist Heimteam, 'away' = umgekehrt
    tag_ids TEXT,
    image TEXT
);
CREATE TABLE IF NOT EXISTS series_map (
    series_id TEXT PRIMARY KEY,
    code TEXT
);
CREATE TABLE IF NOT EXISTS markets (
    condition_id TEXT PRIMARY KEY,
    event_id TEXT,
    code TEXT,
    is_sports INTEGER DEFAULT 1,
    market_type TEXT,             -- moneyline, totals, spreads, ...
    question TEXT,
    event_title TEXT,
    event_slug TEXT,
    group_item TEXT,
    outcome0 TEXT, outcome1 TEXT,
    token0 TEXT, token1 TEXT,
    line REAL,
    game_start INTEGER,
    closed INTEGER DEFAULT 0,
    price0 REAL, price1 REAL,     -- letzte Preise bzw. Auflösung (1/0/0.5)
    best_bid REAL, best_ask REAL, -- Orderbuch für outcome0
    liquidity REAL,
    volume REAL,
    fee_rate REAL,
    score TEXT,
    updated INTEGER
);
CREATE INDEX IF NOT EXISTS ix_markets_event ON markets(event_id);
CREATE INDEX IF NOT EXISTS ix_markets_open ON markets(closed, game_start);

CREATE TABLE IF NOT EXISTS games (
    event_id TEXT PRIMARY KEY,
    code TEXT,
    pool TEXT,
    family TEXT,
    title TEXT,
    start_ts INTEGER,
    side_a TEXT, side_b TEXT,
    home TEXT,                    -- 'a', 'b' oder NULL (neutral)
    score_a REAL, score_b REAL,
    score_raw TEXT,
    result TEXT,                  -- 'a', 'b', 'draw'
    shape TEXT,                   -- 'h2h' (zwei Namen) oder '3way' (Ja/Nein pro Team, Fußball)
    token_a TEXT, token_b TEXT, token_draw TEXT
);
CREATE INDEX IF NOT EXISTS ix_games_pool ON games(pool, start_ts);

CREATE TABLE IF NOT EXISTS closing_prices (
    token_id TEXT PRIMARY KEY,
    ts INTEGER,
    price REAL
);

CREATE TABLE IF NOT EXISTS predictions (
    condition_id TEXT,
    outcome_index INTEGER,
    model TEXT,
    prob REAL,
    n_games INTEGER,              -- Vertrauen: min. Anzahl Spiele der beiden Seiten im Modell
    updated INTEGER,
    PRIMARY KEY (condition_id, outcome_index)
);

CREATE TABLE IF NOT EXISTS wallets (
    address TEXT PRIMARY KEY,
    name TEXT,
    join_ts INTEGER,
    trades INTEGER,
    all_pnl REAL,
    all_volume REAL,
    sports_pnl REAL,
    sports_volume REAL,
    sports_rank INTEGER,
    label TEXT,
    updated INTEGER
);

CREATE TABLE IF NOT EXISTS whale_trades (
    id TEXT PRIMARY KEY,          -- tx_hash:token_id:wallet
    ts INTEGER,
    wallet TEXT,
    condition_id TEXT,
    token_id TEXT,
    side TEXT,
    outcome TEXT,
    outcome_index INTEGER,
    size REAL,
    price REAL,
    usd REAL,
    title TEXT,
    event_slug TEXT,
    code TEXT,
    model_prob REAL,
    notified INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_whale_ts ON whale_trades(ts);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created INTEGER,
    event_time INTEGER,
    description TEXT,
    family TEXT,
    condition_id TEXT,
    outcome_index INTEGER,
    odds REAL,                    -- Dezimalquote
    stake REAL,
    bookmaker TEXT,
    model_prob REAL,
    status TEXT DEFAULT 'open',   -- open, won, lost, void
    payout REAL,
    settled_ts INTEGER,
    closing_odds REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS price_moves (
    condition_id TEXT PRIMARY KEY,
    move0 REAL,                   -- Preisänderung outcome0 in den letzten 24 h
    updated INTEGER
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.DB_PATH, timeout=60)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


MIGRATIONS = [
    ("predictions", "n_games", "INTEGER"),
    ("predictions", "injury_adj", "REAL"),
    ("predictions", "injury_note", "TEXT"),
]


def init() -> None:
    with conn() as con:
        con.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            cols = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
            if col not in cols:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def upsert(con: sqlite3.Connection, table: str, rows: list[dict[str, Any]], key: str | tuple[str, ...]) -> None:
    """INSERT ... ON CONFLICT DO UPDATE für eine Liste gleich aufgebauter Dicts."""
    if not rows:
        return
    cols = list(rows[0].keys())
    keys = (key,) if isinstance(key, str) else key
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in keys)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
        f"ON CONFLICT({', '.join(keys)}) DO "
        + (f"UPDATE SET {updates}" if updates else "NOTHING")
    )
    con.executemany(sql, [tuple(r.get(c) for c in cols) for r in rows])


def df(sql: str, params: tuple | list = ()) -> pd.DataFrame:
    with conn() as con:
        return pd.read_sql_query(sql, con, params=params)


def kv_get(key: str, default: Any = None) -> Any:
    with conn() as con:
        r = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def kv_set(key: str, value: Any) -> None:
    with conn() as con:
        con.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )


def now() -> int:
    return int(time.time())
