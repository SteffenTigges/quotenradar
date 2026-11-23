"""Gemeinsame Helfer für das Dashboard."""
from __future__ import annotations

import time

import pandas as pd
import streamlit as st

from .. import db
from ..families import odds_link as _odds_link

FAMILY_DE = {
    "soccer": "⚽ Fußball",
    "basketball": "🏀 Basketball",
    "tennis": "🎾 Tennis",
    "american-football": "🏈 American Football",
    "hockey": "🏒 Eishockey",
    "baseball": "⚾ Baseball",
    "esports": "🎮 Esports",
    "mma": "🥊 MMA/UFC",
    "boxing": "🥊 Boxen",
    "darts": "🎯 Darts",
    "table-tennis": "🏓 Tischtennis",
    "cricket": "🏏 Cricket",
    "handball": "🤾 Handball",
    "volleyball": "🏐 Volleyball",
    "rugby": "🏉 Rugby",
    "aussie-rules": "🏉 Aussie Rules",
    "golf": "⛳ Golf",
    "racing": "🏎️ Motorsport",
    "chess": "♟️ Schach",
    "pickleball": "🏓 Pickleball",
    "lacrosse": "🥍 Lacrosse",
    "other": "Sonstiges",
}

MARKET_DE = {
    "moneyline": "Sieger",
    "spreads": "Handicap",
    "totals": "Über/Unter",
    "both_teams_to_score": "Beide treffen",
    "soccer_team_totals": "Team-Tore",
    "team_totals": "Team-Punkte",
}

LABEL_ICON = {"Sharp": "🟢 Profi", "Neutral": "⚪ Neutral", "Frische Wallet": "🟡 Neues Konto", "Dumb Money": "🔴 Verlierer", "Unbekannt": "❔ Unbekannt"}


def fam(x: str | None) -> str:
    return FAMILY_DE.get(x or "other", x or "Sonstiges")


def ago(ts: int | None) -> str:
    if not ts:
        return "nie"
    s = int(time.time() - ts)
    if s < 90:
        return f"vor {s} s"
    if s < 5400:
        return f"vor {s // 60} min"
    if s < 172800:
        return f"vor {s // 3600} h"
    return f"vor {s // 86400} Tagen"


def odds_link(title: str | None, family: str | None = None) -> str | None:
    """Quotenvergleich bei OddsPortal (in Deutschland erreichbar, zeigt u. a. Tipico, bet365, Betano, Oddset)."""
    return _odds_link(title or "", family)


def money(usd: float | None) -> str:
    """Dollar-Betrag (Polymarket) als Euro anzeigen."""
    from .. import fx

    return "–" if usd is None or pd.isna(usd) else fx.euro(fx.to_eur(usd))


@st.cache_data(ttl=60, show_spinner=False)
def cached_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    return db.df(sql, params)


def has_data() -> bool:
    try:
        return bool(db.kv_get("backfill_days"))
    except Exception:  # noqa: BLE001
        return False


def no_data_hint() -> None:
    st.info(
        "Noch keine Daten. Gehe auf **⚙️ Daten** und klicke auf *Ersteinrichtung starten*, "
        "oder führe im Terminal `python -m edgelab setup` aus."
    )


def run_with_status(label: str, fn, *args, **kwargs):
    """Führt eine längere Aufgabe aus und zeigt Fortschritt im UI."""
    with st.status(label, expanded=True) as status:
        def progress(msg: str) -> None:
            status.write(msg)

        try:
            out = fn(*args, progress=progress, **kwargs)
        except Exception as e:  # noqa: BLE001
            status.update(label=f"Fehler: {e}", state="error")
            raise
        status.update(label=f"{label} – fertig", state="complete", expanded=False)
    st.cache_data.clear()
    return out
