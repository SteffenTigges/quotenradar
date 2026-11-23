"""Alle Seiten des Dashboards."""
from __future__ import annotations


import altair as alt
import numpy as np
import pandas as pd
import streamlit as st

from .. import backtest, betting, bookmakers, config, db, games, ingest, injuries, modeling, notify, positions, predict, props, whales
from .. import polymarket as pm
from ..families import excluded, outcome_de
from .. import fx
from .common import (
    FAMILY_DE, LABEL_ICON, MARKET_DE, ago, cached_df, fam, has_data, money, no_data_hint, odds_link, run_with_status,
)

PCT = st.column_config.NumberColumn(format="percent")


# ============================================================ Daten laden (gecacht)


@st.cache_data(ttl=60, show_spinner=False)
def load_edges() -> pd.DataFrame:
    d = predict.edges()
    if d.empty:
        return d
    d["Sportart"] = d["family"].map(fam)
    d["Markt"] = d["market_type"].map(lambda x: MARKET_DE.get(x, x))
    d["Link"] = [odds_link(t, f) for t, f in zip(d["event_title"], d["family"])]
    d["liquidity"] = d["liquidity"] / fx.eur_usd()
    return d


@st.cache_data(ttl=30, show_spinner=False)
def load_feed(hours: int) -> pd.DataFrame:
    d = whales.feed(hours)
    if d.empty:
        return d
    d["Sportart"] = d["family"].map(fam)
    d["Kontotyp"] = d["label"].map(lambda x: LABEL_ICON.get(x, x))
    d["outcome"] = [outcome_de(o, t) for o, t in zip(d["outcome"], d["title"])]
    d["Link"] = [odds_link(t, f) for t, f in zip(d["title"], d["family"])]
    d["live"] = d["game_start"].notna() & (d["ts"] > d["game_start"])
    return d


def edge_filter_defaults(
    d: pd.DataFrame, min_n: int, min_liq: float, show_suspicious: bool, show_news: bool = False
) -> pd.DataFrame:
    d = d[(d["n_games"].fillna(0) >= min_n) & (d["liquidity"].fillna(0) >= min_liq)]
    if not show_suspicious:
        d = d[~d["suspicious"]]
    if not show_news:
        d = d[~d["news_alert"]]
    return d


# ============================================================ Übersicht


def page_overview() -> None:
    st.title("📈 EdgeLab")
    st.caption("Prognosen, Quotenvergleich, Großwetten und deine Wetten – kostenlos, nur für dich.")
    if not has_data():
        no_data_hint()
        return

    k = cached_df(
        "SELECT (SELECT COUNT(*) FROM games) games, (SELECT COUNT(DISTINCT pool) FROM games) pools, "
        "(SELECT COUNT(*)/2 FROM predictions p JOIN markets m USING(condition_id) WHERE m.closed=0 AND m.game_start > strftime('%s','now')) priced, "
        "(SELECT COUNT(*) FROM whale_trades WHERE ts > strftime('%s','now') - 86400) whales24"
    ).iloc[0]
    c = st.columns(4)
    c[0].metric("Spiele in der Datenbank", f"{int(k['games']):,}".replace(",", "."))
    c[1].metric("Bepreiste offene Märkte", f"{int(k['priced'] or 0):,}".replace(",", "."))
    c[2].metric("Großwetten (24 h)", int(k["whales24"] or 0))
    pos = positions.table()
    if not pos.empty:
        s = positions.stats(pos)
        c[3].metric("Deine Rendite", f"{fx.prozent(s['roi'], 1, vorzeichen=True)}", f"{fx.euro(s['profit'], 2)} Gewinn")
    else:
        c[3].metric("Deine Rendite", "–", "noch keine Wetten")

    c1, c2, c3 = st.columns([1, 1, 2])
    c1.write(f"Prognosen: **{ago(db.kv_get('last_predict'))}**")
    c2.write(f"Großwetten: **{ago(db.kv_get('last_whale_poll'))}**")
    with c3:
        b1, b2 = st.columns(2)
        if b1.button("🔄 Alles aktualisieren", width="stretch"):
            run_with_status("Aktualisiere Märkte, Modelle und Prognosen", _update_job)
            st.rerun()
        if b2.button("🐋 Großwetten abrufen", width="stretch"):
            run_with_status("Suche große Wetten", whales.poll)
            st.rerun()

    st.subheader("Beste Edges jetzt")
    st.caption("Nur Prognosen mit mindestens 10 Spielen Historie je Seite, ≥ 500 € Liquidität und ohne verdächtig große Abweichung vom Markt.")
    e = load_edges()
    if e.empty:
        st.write("Noch keine Prognosen.")
    else:
        top = edge_filter_defaults(e, 10, 500, False)
        top = top[(top["start"] > pd.Timestamp.now(tz="Europe/Berlin")) & (top["ev"] > 0)].head(10)
        _edge_table(top, key="ov_edges", selectable=False)

    st.subheader("Neueste Profi-Wetten")
    f = load_feed(24)
    if f.empty:
        st.write("Noch keine Großwetten – klicke auf *Großwetten abrufen*.")
    else:
        f = f[(f["label"] == "Sharp") & (f["side"] == "BUY") & (f["price"] < 0.95)].head(8)
        _whale_table(f, key="ov_whales")

    _disclaimer()


def _update_job(progress) -> None:
    progress("Neue Ergebnisse laden …")
    ingest.backfill(days=3)
    ingest.refresh_stale()
    progress("Offene Märkte laden (ca. 1 Minute) …")
    n = ingest.refresh_open()
    progress(f"{n} offene Märkte. Modelle trainieren …")
    try:
        injuries.refresh(progress=progress)
    except Exception as e:  # noqa: BLE001
        progress(f"Verletzungen nicht geladen: {e}")
    models = modeling.train_all()
    progress(f"{len(models)} Modelle trainiert. Prognosen berechnen …")
    predict.run(models, progress=progress)
    k = positions.auto_settle()
    if k:
        progress(f"{k} Positionen abgerechnet.")


def _disclaimer() -> None:
    st.caption(
        "⚠️ Nur zur Information und Unterhaltung, keine Finanz- oder Wettberatung. Modelle irren sich oft – "
        "prüfe im **Backtest**, ob ein Modell den Markt überhaupt schlägt. Wetten kann süchtig machen "
        "(Hilfe: check-dein-spiel.de, 0800 1 37 27 00)."
    )


# ============================================================ Quotenvergleich


def books_table(g: dict) -> None:
    """Tipico | Betano | Winamax neben der fairen Modellquote; Value grün markiert."""
    rows = games.books_rows(g)
    if not rows:
        if not bookmakers.enabled():
            st.caption("Quotenvergleich Tipico/Betano/Winamax: kostenlosen Schlüssel von the-odds-api.com in `.env` eintragen (siehe README).")
        else:
            st.caption("Für dieses Spiel liefern Tipico, Betano und Winamax (noch) keine Quoten.")
        st.link_button("Quoten auf OddsPortal ansehen", odds_link(f"{g['a']} vs. {g['b']}", g["family"]))
        return
    df = pd.DataFrame(rows)
    df["value"] = [None if u else v for u, v in zip(df["unsure"], df["value"])]
    df["best"] = [f"{b} ⚠️ Modell unsicher" if u else b for u, b in zip(df["unsure"], df["best"])]
    df = df.rename(columns={"tipp": "Tipp", "p": "Modell", "fair": "Faire Quote", "best": "Beste Quote bei", "value": "Value"})
    st.dataframe(
        df[["Tipp", "Modell", "Faire Quote"] + [b for b in games.book_list(g) if b in df.columns] + ["Beste Quote bei", "Value"]],
        hide_index=True, width="stretch",
        column_config={"Modell": PCT, "Value": st.column_config.NumberColumn(format="percent", help="Beste Quote × Modellwahrscheinlichkeit − 1. Positiv = laut Modell lohnend."),
                       "Faire Quote": st.column_config.NumberColumn(format="localized")} | {b: st.column_config.NumberColumn(format="localized") for b in games.book_list(g)},
    )
    st.caption("Grün markiert ist die beste Quote. Betano-Quoten aus der App stammen von Betano UK und können leicht von betano.de abweichen.")


# ============================================================ Spiele


@st.cache_data(ttl=300, show_spinner=False)
def load_games(hours: int) -> list[dict]:
    return games.build(hours)


def _prob_bar(pr: dict) -> str:
    a, x, b = pr.get("a") or 0, pr.get("draw") or 0, pr.get("b") or 0
    t = a + x + b or 1
    seg = lambda w, c: f'<span style="display:inline-block;height:10px;width:{w / t * 100:.1f}%;background:{c}"></span>'
    return ('<div style="display:flex;border-radius:5px;overflow:hidden;background:#8883">'
            + seg(a, "#2f5bea") + (seg(x, "#8a96a8") if x else "") + seg(b, "#c08a2e") + "</div>")


def page_games() -> None:
    st.title("🏟️ Spiele")
    st.caption("Wer gewinnt mit welcher Wahrscheinlichkeit – Modell und Markt – plus Wettangebot mit fairen Quoten und NBA-Spielerwetten.")
    if not has_data():
        no_data_hint()
        return
    c = st.columns([1, 2, 1])
    hours = c[0].select_slider("Zeitraum", [12, 24, 48], value=24, format_func=lambda h: f"{h} h")
    q = c[1].text_input("Suche", placeholder="Team, Spieler-Team oder Liga")
    if c[2].button("🏀 Spielerdaten laden", width="stretch", help="NBA-Kader und Spiellogs von ESPN (ca. 1 Minute)"):
        run_with_status("Lade NBA-Spielerdaten", props.refresh)
        st.rerun()
    with st.spinner("Spiele werden aufbereitet …"):
        G = load_games(hours)
    if not G:
        st.info("Keine Spiele im Zeitraum. Aktualisiere die Daten in der Übersicht.")
        return
    fams = sorted({g["family"] for g in G}, key=fam)
    sel = st.pills("Sportart", fams, format_func=fam, selection_mode="multi")
    only_model = st.toggle("Nur Spiele mit Modellprognose", value=True)
    G = [g for g in G if (not sel or g["family"] in sel) and (not only_model or g["model"])
         and (not q or q.lower() in f"{g['a']} {g['b']} {g['league']}".lower())]
    st.caption(f"{len(G)} Spiele")
    labels = [f"{fx.datum(g['start'])} · {fam(g['family'])} · {g['league']} · {g['a']} – {g['b']}" for g in G]
    if not G:
        return
    idx = st.selectbox("Spiel öffnen", range(len(G)), format_func=lambda i: labels[i])

    for g in G[:60]:
        pr = g["model"] or g["market"]
        with st.container(border=True):
            cc = st.columns([3, 1, 3])
            cc[0].markdown(f"**{g['a']}**  \n### {fx.prozent(pr.get('a', 0), 0)}")
            cc[1].markdown(f"<div style='text-align:center'>{'Remis<br><b>' + format(pr['draw'], '.0%') + '</b>' if pr.get('draw') is not None else 'vs.'}</div>", unsafe_allow_html=True)
            cc[2].markdown(f"<div style='text-align:right'><b>{g['b']}</b><h3 style='margin:0'>{fx.prozent(pr.get('b', 0), 0)}</h3></div>", unsafe_allow_html=True)
            st.markdown(_prob_bar(pr), unsafe_allow_html=True)
            mk = g["market"]
            st.caption(
                f"{fam(g['family'])} · {g['league']} · {fx.datum(g['start'])} · "
                f"{games.favorite_text(g)} · {'Modell' if g['model'] else 'Markt'}"
                + (f" · Markt: {fx.prozent(mk.get('a', 0), 0)}" + (f" / X {fx.prozent(mk['draw'], 0)}" if mk.get('draw') is not None else "") + f" / {fx.prozent(mk.get('b', 0), 0)}" if g['model'] and mk else "")
                + (" · 🩹 Ausfälle" if g.get("injuries") else "") + (" · 🏀 Spielerwetten" if g.get("props") else "")
            )
    if len(G) > 60:
        st.caption(f"{len(G) - 60} weitere – Filter oder Suche nutzen, oder oben ein Spiel öffnen.")

    g = G[idx]
    st.divider()
    st.subheader(f"{g['a']} – {g['b']}")
    if g.get("darts"):
        da, db_ = g["darts"]["a"], g["darts"]["b"]
        st.markdown("**Darts-Statistik (letzte 12 Monate, Darts Orakel)**" + (f" · Modus: {g['darts']['format']}" if g["darts"].get("format") else ""))
        st.dataframe(pd.DataFrame({
            "Wert": ["3-Dart-Average", "First 9 Average", "180er", "Checkout-Quote", "Gewonnene Legs", "Höchstes Finish"],
            g["a"]: [fx.zahl(da["avg"], 2), fx.zahl(da["first9"], 2), fx.zahl(da["s180"]), fx.prozent((da["checkout"] or 0) / 100), fx.prozent((da["legs_won"] or 0) / 100), fx.zahl(da["high_co"])],
            g["b"]: [fx.zahl(db_["avg"], 2), fx.zahl(db_["first9"], 2), fx.zahl(db_["s180"]), fx.prozent((db_["checkout"] or 0) / 100), fx.prozent((db_["legs_won"] or 0) / 100), fx.zahl(db_["high_co"])],
        }), hide_index=True, width="stretch")
    st.markdown("**Quotenvergleich**")
    books_table(g)
    if g.get("expected"):
        st.write(f"Erwartetes Ergebnis: **{g['expected']}** · Methode: {g.get('method') or '–'}")
    if g.get("injuries"):
        st.warning("Ausfälle (eingerechnet): " + g["injuries"])
    for grp in g["groups"]:
        st.markdown(f"**{grp['group']}**")
        cols = st.columns(min(len(grp["items"]), 4))
        for i, it in enumerate(grp["items"]):
            cols[i % len(cols)].metric(it["label"], f"{fx.prozent(it['p'], 0)}", f"Quote {fx.zahl(it['odds'], 2)}" if it["odds"] else None, delta_color="off")
    if g.get("props"):
        st.markdown("**Spielerwetten** (Linie = Saisonschnitt mit ,5)")
        rows = []
        for p_ in g["props"]:
            for stt in p_["stats"]:
                pmx = stt.get("pm") or {}
                rows.append({"Team": p_["team"], "Spieler": p_["player"], "Min.": p_["min"], "Wette": f"{stt['label']} über {str(stt['line']).replace('.', ',')}",
                             "Ø": stt["avg"], "Wahrscheinlichkeit": stt["p_over"], "Faire Quote": 1 / stt["p_over"] if stt["p_over"] else None,
                             "Polymarket-Linie": pmx.get("line"), "PM Preis Über": pmx.get("price_over"), "Modell PM-Linie": pmx.get("p_over"),
                             "EV Über": pmx.get("ev_over"), "EV Unter": pmx.get("ev_under")})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                     column_config={"Wahrscheinlichkeit": PCT, "Modell PM-Linie": PCT, "EV Über": PCT, "EV Unter": PCT,
                                    "Faire Quote": st.column_config.NumberColumn(format="localized")})
    if g.get("polymarket"):
        st.markdown("**Polymarket-Preise zum Vergleich** (Datenquelle; wetten kannst du dort aus Deutschland nicht)")
        st.dataframe(pd.DataFrame(g["polymarket"]).rename(columns={"pick": "Tipp", "market": "Markt", "p": "Modell", "price": "Preis", "ev": "EV", "liq": "Liquidität", "news": "News"}),
                     hide_index=True, width="stretch", column_config={"Modell": PCT, "EV": PCT})


# ============================================================ Prognosen


def _edge_table(d: pd.DataFrame, key: str, selectable: bool = True):
    view = pd.DataFrame(
        {
            "Start": d["start"].map(fx.wochentag_zeit),
            "Sportart": d["Sportart"],
            "Liga": d["league"],
            "Spiel": d["match"],
            "Tipp": d["pick"],
            "Modell": d["prob"],
            "Preis": d["price"],
            "Faire Quote": d["fair_odds"],
            "Marktquote": d["market_odds"],
            "EV": d["ev"],
            "Kelly": d["kelly"],
            "Liquidität": d["liquidity"],
            "Vertrauen": d["n_games"],
            "Methode": d["model"],
            "Markt 24h": d["move24"],
            "Ausfälle": d["injury_note"].fillna("").map(lambda x: x[:140]),
            "Link": d["Link"],
        }
    )
    view.insert(4, "⚑", [("📰" if n else "") + ("🩹" if a else "") for n, a in zip(d["news_alert"], d["injury_adj"].fillna(0) != 0)])
    cfg = {
        "Modell": st.column_config.NumberColumn(format="percent", help="Modellwahrscheinlichkeit"),
        "Preis": st.column_config.NumberColumn(format="localized", help="Aktueller Kaufpreis auf Polymarket (= implizite Wahrscheinlichkeit)"),
        "Faire Quote": st.column_config.NumberColumn(format="localized"),
        "Marktquote": st.column_config.NumberColumn(format="localized"),
        "EV": st.column_config.NumberColumn(format="percent", help="Erwarteter Gewinn pro eingesetztem Euro inkl. Polymarket-Gebühr"),
        "Kelly": st.column_config.NumberColumn(format="percent", help="Voller Kelly-Anteil der Bankroll – in der Praxis ¼ davon"),
        "Liquidität": st.column_config.NumberColumn(format="euro"),
        "Vertrauen": st.column_config.NumberColumn(help="Mindestanzahl Spiele, die das Modell von beiden Seiten kennt"),
        "Link": st.column_config.LinkColumn(display_text="Quoten vergleichen", help="OddsPortal: Quoten deutscher Anbieter (Tipico, bet365, Betano, Oddset …)"),
        "Markt 24h": st.column_config.NumberColumn(format="localized", help="Preisänderung dieses Tipps in 24 h. Stark negativ = Markt weiß vermutlich etwas (Verletzung, Aufstellung)"),
        "⚑": st.column_config.TextColumn(help="📰 Markt hat sich stark gegen den Tipp bewegt (News?) · 🩹 Verletzungen eingerechnet"),
    }
    if selectable:
        return st.dataframe(view, column_config=cfg, hide_index=True, width="stretch", key=key,
                            on_select="rerun", selection_mode="single-row")
    st.dataframe(view, column_config=cfg, hide_index=True, width="stretch", key=key)
    return None


def page_predictions() -> None:
    st.title("🎯 Value-Tipps")
    if not has_data():
        no_data_hint()
        return
    e = load_edges()
    if e.empty:
        st.info("Noch keine Prognosen. Klicke in der Übersicht auf *Alles aktualisieren*.")
        return

    with st.container(border=True):
        c = st.columns(4)
        fams = sorted(e["family"].dropna().unique(), key=fam)
        sel_f = c[0].multiselect("Sportart", fams, format_func=fam, placeholder="Alle")
        leagues = sorted(e[e["family"].isin(sel_f)]["league"].dropna().unique()) if sel_f else sorted(e["league"].dropna().unique())
        sel_l = c[1].multiselect("Liga", leagues, placeholder="Alle")
        mts = sorted(e["market_type"].unique())
        sel_m = c[2].multiselect("Markt", mts, format_func=lambda x: MARKET_DE.get(x, x), placeholder="Alle")
        hours = c[3].select_slider("Zeitraum", [6, 12, 24, 48, 72], value=48, format_func=lambda h: f"nächste {h} h")
        c = st.columns(5)
        min_ev = c[0].number_input("Min. EV %", value=0, step=1) / 100
        min_n = c[1].number_input("Min. Vertrauen (Spiele)", value=10, step=1, min_value=0)
        min_liq = c[2].number_input("Min. Liquidität €", value=500, step=250, min_value=0)
        search = c[3].text_input("Suche (Team/Spieler)")
        show_susp = c[4].toggle("Verdächtige zeigen", value=False, help=f"Abweichung zum Markt > {fx.prozent(predict.SUSPICIOUS_EDGE, 0)} – meist Modellfehler")
        show_news = c[4].toggle("📰 News-Alarme zeigen", value=False, help="Tipps, gegen die sich der Markt in 24 h stark bewegt hat")

    d = edge_filter_defaults(e, int(min_n), float(min_liq), show_susp, show_news)
    now = pd.Timestamp.now(tz="Europe/Berlin")
    d = d[(d["start"] > now) & (d["start"] < now + pd.Timedelta(hours=hours)) & (d["ev"] >= min_ev)]
    if sel_f:
        d = d[d["family"].isin(sel_f)]
    if sel_l:
        d = d[d["league"].isin(sel_l)]
    if sel_m:
        d = d[d["market_type"].isin(sel_m)]
    if search:
        d = d[d["event_title"].str.contains(search, case=False, na=False)]
    d = d.reset_index(drop=True)

    st.caption(f"{len(d)} Tipps · sortiert nach EV · Klicke eine Zeile an, um sie als Position zu speichern.")
    ev = _edge_table(d.head(500), key="pred_table")
    rows = ev.selection.rows if ev else []
    if rows:
        r = d.iloc[rows[0]]
        with st.container(border=True):
            st.markdown(f"**{r['match']}** – {r['pick']} · Modell {fx.prozent(r['prob'], 1)} · Preis {fx.zahl(r['price'], 3)} · EV {fx.prozent(r['ev'], 1, vorzeichen=True)}")
            c = st.columns([1, 1, 1, 1])
            bank = c[0].number_input("Bankroll (€)", value=float(st.session_state.get("bankroll", 1000.0)), step=100.0, format="%.0f")
            st.session_state["bankroll"] = bank
            frac = c[1].selectbox("Kelly-Anteil", [0.25, 0.5, 1.0], format_func=lambda x: {0.25: "¼ Kelly", 0.5: "½ Kelly", 1.0: "voller Kelly"}[x])
            stake = c[2].number_input("Einsatz (€)", value=round(max(1.0, bank * r["kelly"] * frac), 2), step=1.0)
            if c[3].button("➕ Als Position speichern", width="stretch"):
                positions.add(
                    description=f"{r['match']}: {r['pick']}",
                    odds=float(r["market_odds"]),
                    stake=float(stake),
                    family=r["family"],
                    condition_id=r["condition_id"],
                    outcome_index=int(r["outcome_index"]),
                    model_prob=float(r["prob"]),
                    event_time=int(r["game_start"]),
                )
                st.success("Gespeichert – siehe 📒 Positionen.")
    with st.expander("Was bedeuten die Spalten?"):
        st.markdown(
            "- **Modell**: Wahrscheinlichkeit laut unserem Modell (Elo, Poisson-Tormodell oder Punktemodell).\n"
            "- **Preis**: Was ein Anteil auf Polymarket jetzt kostet. Gewinnt der Tipp, zahlt er 1 $ aus (Polymarket rechnet in Dollar). "
            "Der Preis ist also die Wahrscheinlichkeit, die der Markt sieht.\n"
            "- **EV**: Erwarteter Gewinn je eingesetztem Dollar, nach Polymarket-Gebühr (rate × p × (1−p)).\n"
            "- **Kelly**: Optimaler Anteil der Bankroll *wenn das Modell stimmt*. Nimm höchstens ¼ davon.\n"
            "- **Vertrauen**: Wie viele Spiele das Modell mindestens von beiden Seiten gesehen hat. "
            "Unter ~15 sind die Prognosen wackelig.\n"
            "- Ein positiver EV heißt nur: Modell und Markt sind unterschiedlicher Meinung. Meist hat der Markt recht – "
            "schau im **Backtest**, für welche Sportarten das Modell überhaupt etwas taugt."
        )
    _disclaimer()


# ============================================================ Whale Tracker


def _whale_table(f: pd.DataFrame, key: str) -> None:
    def verdict(r) -> str:
        if pd.isna(r["model_prob"]):
            return "–"
        diff = r["model_prob"] - r["price"]
        if r["side"] == "SELL":
            diff = -diff
        return f"{'✅ stimmt zu' if diff > 0 else '❌ widerspricht'} ({fx.prozent(diff, 1, vorzeichen=True)})"

    view = pd.DataFrame(
        {
            "Zeit": f["time"].dt.strftime("%d.%m. %H:%M"),
            "Betrag": f["eur"],
            "Sportart": f["Sportart"],
            "Markt": f["title"],
            "Aktion": f["side"].map({"BUY": "Kauf", "SELL": "Verkauf"}),
            "Tipp": f["outcome"],
            "Preis": f["price"],
            "Live": f["live"].map({True: "🔴 live", False: ""}),
            "Kontotyp": f["Kontotyp"],
            "Konto": f["name"].where(f["name"].fillna("").str.len().between(1, 30), f["wallet"].str[:10]),
            "Sportgewinn": f["pnl_eur"],
            "Modell": f.apply(verdict, axis=1),
            "Link": f["Link"],
            "Adresse": f["wallet"],
        }
    )
    st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        key=key,
        column_config={
            "Betrag": st.column_config.NumberColumn(format="euro"),
            "Preis": st.column_config.NumberColumn(format="localized"),
            "Sportgewinn": st.column_config.NumberColumn(format="euro", help="Gesamtgewinn des Kontos in Sportmärkten (Polymarket-Rangliste)"),
            "Link": st.column_config.LinkColumn(display_text="Quoten vergleichen", help="OddsPortal: Quoten deutscher Anbieter (Tipico, bet365, Betano, Oddset …)"),
            "Adresse": None,
        },
    )


def _whales_by_game(f: pd.DataFrame) -> None:
    """Pro Spiel: wohin das große Geld fließt, die einzelnen Wetten und darunter der Quotenvergleich."""
    from ..families import parse_sides

    try:
        G = load_games(48)
    except Exception:  # noqa: BLE001
        G = []
    f = f.assign(_sides=f["title"].map(lambda t: parse_sides(t or "")))
    f = f.assign(_key=f["_sides"].map(lambda s: " vs. ".join(s) if s else None)).dropna(subset=["_key"])
    order = f.groupby("_key")["eur"].sum().sort_values(ascending=False)
    for key in order.index[:40]:
        part = f[f["_key"] == key]
        a, b = part["_sides"].iloc[0]
        g = next((x for x in G if {x["a"].lower(), x["b"].lower()} == {a.lower(), b.lower()}), None)
        by_out = part.groupby("outcome")["eur"].sum().sort_values(ascending=False)
        sharp = int((part["label"] == "Sharp").sum())
        head = f"**{a} – {b}** · {part['Sportart'].iloc[0]} · {fx.euro(part['eur'].sum())} in {len(part)} Wetten" + (f" · 🟢 {sharp} Profi" if sharp else "")
        with st.expander(head, expanded=bool(sharp)):
            st.write("Geldfluss: " + " · ".join(f"**{o}** {fx.euro(v)}" for o, v in by_out.items()))
            _whale_table(part, key=f"wg_{abs(hash(key))}")
            if g is not None:
                pr = g["model"] or g["market"]
                st.caption(f"Modell: {g['a']} {fx.prozent(pr.get('a', 0), 0)}" + (f" · X {fx.prozent(pr['draw'], 0)}" if pr.get("draw") is not None else "") + f" · {g['b']} {fx.prozent(pr.get('b', 0), 0)}")
                books_table(g)
            else:
                st.link_button("Quoten auf OddsPortal ansehen", odds_link(key, part["family"].iloc[0]))


@st.cache_data(ttl=600, show_spinner=False)
def _pm_leaderboard(period: str) -> pd.DataFrame:
    return pd.DataFrame(pm.leaderboard("sports", period, "PNL", 50))


def page_whales() -> None:
    st.title("🐋 Großwetten (Whale Tracker)")
    st.caption(f"Große Sportwetten auf Polymarket (ab {fx.euro(fx.to_eur(config.WHALE_MIN_USD))}), Konten nach ihrer Bilanz eingeordnet und mit unserem Modell verglichen.")
    if not has_data():
        no_data_hint()
        return
    c = st.columns([3, 1])
    c[0].write(f"Letzter Abruf: **{ago(db.kv_get('last_whale_poll'))}** – für Echtzeit `python -m edgelab run` im Hintergrund laufen lassen.")
    if c[1].button("🐋 Jetzt abrufen", width="stretch"):
        run_with_status("Suche große Wetten", whales.poll)
        st.rerun()

    tab1, tab2, tab3 = st.tabs(["Aktuell", "Ranglisten", "Konto nachschlagen"])
    with tab1:
        with st.container(border=True):
            c = st.columns(5)
            hours = c[0].select_slider("Zeitraum", [1, 3, 6, 12, 24, 48, 168], value=24, format_func=lambda h: f"{h} h")
            min_usd = c[1].number_input("Min. Betrag €", value=int(round(fx.to_eur(config.WHALE_MIN_USD) or 0, -2)), step=500, min_value=0)
            labels = c[2].multiselect("Kontotyp", list(LABEL_ICON), format_func=lambda x: LABEL_ICON[x], placeholder="Alle")
            hide_sure = c[3].toggle("Fast sichere ausblenden", value=True, help="Käufe zu ≥ 0,95 – meist kurz vor Schluss, ohne Aussagekraft")
            buys = c[4].toggle("Nur Käufe", value=True)
        f = load_feed(hours)
        if f.empty:
            st.info("Noch keine Whale-Wetten im Zeitraum.")
            return
        f = f[f["eur"] >= min_usd]
        if labels:
            f = f[f["label"].isin(labels)]
        if hide_sure:
            f = f[(f["price"] < 0.95) & (f["price"] > 0.05)]
        if buys:
            f = f[f["side"] == "BUY"]
        fams = sorted(f["family"].dropna().unique(), key=fam)
        sel = st.pills("Sportart", fams, format_func=fam, selection_mode="multi")
        if sel:
            f = f[f["family"].isin(sel)]

        k = st.columns(4)
        k[0].metric("Erfasste Wetten", len(f))
        k[1].metric("Größte", fx.euro(f["eur"].max()) if len(f) else "–")
        k[2].metric("Profi-Anteil", f"{fx.prozent((f['label'] == 'Sharp').mean(), 0)}" if len(f) else "–")
        withm = f.dropna(subset=["model_prob"])
        agree = ((withm["model_prob"] - withm["price"]) * np.where(withm["side"] == "SELL", -1, 1) > 0).mean() if len(withm) else None
        k[3].metric("Modell stimmt zu", f"{fx.prozent(agree, 0)}" if agree is not None else "–", help="Anteil der Großwetten, bei denen unser Modell auch Value sieht")
        c1, c2 = st.columns(2)
        grouped = c1.toggle("Nach Spiel gruppieren", value=True)
        if not c2.toggle("Live-Wetten zeigen", value=False, help="Wetten nach Spielbeginn – zum Nachwetten meist nutzlos"):
            f = f[~f["live"]]
        if grouped:
            _whales_by_game(f)
        else:
            _whale_table(f.head(500), key="whale_feed")

    with tab2:
        st.subheader("Beste Konten je Sportart (aus deinen getrackten Wetten)")
        st.caption("Gewinn, wenn die getrackten Wetten bis zur Auflösung gehalten wurden. Je länger der Tracker läuft, desto aussagekräftiger.")
        lb = whales.leaderboard_by_family(min_trades=2)
        if lb.empty:
            st.write("Noch keine aufgelösten Whale-Wetten. Lass den Tracker ein paar Tage laufen.")
        else:
            fsel = st.selectbox("Sportart", sorted(lb["family"].dropna().unique(), key=fam), format_func=fam)
            part = lb[lb["family"] == fsel].head(30)
            st.dataframe(
                part[["name", "label", "trades", "einsatz", "pnl", "roi", "trefferquote", "wallet"]].rename(
                    columns={"name": "Konto", "label": "Typ", "trades": "Wetten", "einsatz": "Einsatz", "pnl": "Gewinn", "roi": "Rendite", "trefferquote": "Trefferquote", "wallet": "Adresse"}
                ),
                hide_index=True, width="stretch",
                column_config={"Einsatz": st.column_config.NumberColumn(format="euro"), "Gewinn": st.column_config.NumberColumn(format="euro"), "Rendite": PCT, "Trefferquote": PCT},
            )
        st.subheader("Polymarket-Rangliste Sport (offiziell)")
        period = st.radio("Zeitraum", ["day", "week", "month", "all"], index=2, horizontal=True,
                          format_func={"day": "Tag", "week": "Woche", "month": "Monat", "all": "Gesamt"}.get)
        try:
            lbp = _pm_leaderboard(period).copy()
            lbp["pnl"] = lbp["pnl"] / fx.eur_usd()
            lbp["volume"] = lbp["volume"] / fx.eur_usd()
            st.dataframe(
                lbp[["rank", "user_name", "pnl", "volume", "user_id"]].rename(columns={"rank": "Rang", "user_name": "Konto", "pnl": "Gewinn", "volume": "Umsatz", "user_id": "Adresse"}),
                hide_index=True, width="stretch",
                column_config={"Gewinn": st.column_config.NumberColumn(format="euro"), "Volumen": st.column_config.NumberColumn(format="euro")},
            )
        except Exception as e:  # noqa: BLE001
            st.warning(f"Rangliste nicht erreichbar: {e}")

    with tab3:
        addr = st.text_input("Konto-Adresse (0x…)", placeholder="Adresse aus dem Feed kopieren")
        if addr and addr.startswith("0x") and len(addr) == 42:
            with st.spinner("Lade Konto …"):
                whales.enrich_wallets([addr.lower()], force=True)
                w = db.df("SELECT * FROM wallets WHERE address=?", (addr.lower(),))
                trades = pm.user_trades(addr, 100)
            if not w.empty:
                r = w.iloc[0]
                c = st.columns(4)
                c[0].metric("Kontotyp", LABEL_ICON.get(r["label"], r["label"]))
                c[1].metric("Sportgewinn", money(r["sports_pnl"]))
                c[2].metric("Sportumsatz", money(r["sports_volume"]))
                c[3].metric("Rang Sport", f"#{int(r['sports_rank'])}" if pd.notna(r["sports_rank"]) else "–")
                st.caption(f"Gesamtgewinn {money(r['all_pnl'])} · {int(r['trades'] or 0)} Wetten · dabei seit {pd.to_datetime(r['join_ts'], unit='s').date() if pd.notna(r['join_ts']) else '?'}")
            if trades:
                t = pd.DataFrame(trades)
                t["Zeit"] = pd.to_datetime(t["timestamp"], unit="s", utc=True).dt.tz_convert("Europe/Berlin").dt.strftime("%d.%m. %H:%M")
                t["Betrag"] = t["size"] * t["price"] / fx.eur_usd()
                st.dataframe(t[["Zeit", "title", "side", "outcome", "price", "Betrag"]].rename(columns={"title": "Markt", "side": "Aktion", "outcome": "Tipp", "price": "Preis"}),
                             hide_index=True, width="stretch", column_config={"Betrag": st.column_config.NumberColumn(format="euro")})


# ============================================================ Strategie


def page_strategy() -> None:
    st.title("🧮 Strategie")
    st.caption("Filtere Value-Tipps, lege Einsatzregeln fest und trage eigene Buchmacher-Quoten ein – EV und Einsatz rechnen sich neu.")
    if not has_data():
        no_data_hint()
        return
    e = load_edges()
    if e.empty:
        st.info("Noch keine Prognosen.")
        return
    with st.container(border=True):
        c = st.columns(4)
        min_ev = c[0].slider("Min. EV", 0.0, 0.3, 0.03, 0.01, format="localized")
        min_edge = c[1].slider("Min. Edge (Modell − Preis)", 0.0, 0.2, 0.03, 0.01, format="localized")
        price_rng = c[2].slider("Preisbereich", 0.05, 0.95, (0.15, 0.85), 0.05, help="Sehr niedrige/hohe Preise sind riskanter für Modellfehler")
        hours = c[3].select_slider("Zeitraum", [6, 12, 24, 48, 72], value=24, format_func=lambda h: f"{h} h")
        c = st.columns(4)
        fams = sorted(e["family"].dropna().unique(), key=fam)
        sel_f = c[0].multiselect("Sportarten", fams, format_func=fam, placeholder="Alle")
        sel_m = c[1].multiselect("Märkte", sorted(e["market_type"].unique()), format_func=lambda x: MARKET_DE.get(x, x), placeholder="Alle")
        min_n = c[2].number_input("Min. Vertrauen", value=15, step=1)
        min_liq = c[3].number_input("Min. Liquidität €", value=1000, step=500)
        c = st.columns(4)
        bank = c[0].number_input("Bankroll (€)", value=float(st.session_state.get("bankroll", 1000.0)), step=100.0, format="%.0f")
        st.session_state["bankroll"] = bank
        staking = c[1].selectbox("Einsatz", ["¼ Kelly", "½ Kelly", "Fester Betrag"], index=0)
        flat = c[2].number_input("Fester Einsatz (€)", value=10.0, step=5.0, disabled=staking != "Fester Betrag")
        cap = c[3].slider("Max. Einsatz je Wette (% Bankroll)", 0.5, 10.0, 3.0, 0.5) / 100
        one_per_game = st.toggle("Nur der beste Tipp pro Spiel", value=True)

    d = edge_filter_defaults(e, int(min_n), float(min_liq), False)
    now = pd.Timestamp.now(tz="Europe/Berlin")
    d = d[(d["start"] > now) & (d["start"] < now + pd.Timedelta(hours=hours))]
    d = d[(d["ev"] >= min_ev) & (d["edge"] >= min_edge) & d["price"].between(*price_rng)]
    if sel_f:
        d = d[d["family"].isin(sel_f)]
    if sel_m:
        d = d[d["market_type"].isin(sel_m)]
    if one_per_game:
        d = d.sort_values("ev", ascending=False).drop_duplicates("match")
    d = d.head(200).reset_index(drop=True)
    if d.empty:
        st.info("Keine Tipps mit diesen Filtern.")
        return

    table = pd.DataFrame(
        {
            "Übernehmen": False,
            "Start": d["start"].map(fx.wochentag_zeit),
            "Spiel": d["match"],
            "Tipp": d["pick"],
            "Modell": d["prob"],
            "Deine Quote": d["market_odds"].round(3),
            "Buchmacher": "Polymarket",
        }
    )
    st.caption("Ändere **Deine Quote**, wenn du woanders wettest (z. B. besserer Kurs beim Buchmacher). EV und Einsatz passen sich an.")
    edited = st.data_editor(
        table,
        hide_index=True,
        width="stretch",
        disabled=["Start", "Spiel", "Tipp", "Modell"],
        column_config={
            "Modell": PCT,
            "Deine Quote": st.column_config.NumberColumn(format="localized", min_value=1.01),
            "Übernehmen": st.column_config.CheckboxColumn(help="Anhaken und unten speichern"),
        },
        key="strategy_editor",
    )
    odds = edited["Deine Quote"].astype(float)
    pol = edited["Buchmacher"].str.lower().eq("polymarket")
    fee = d["fee_rate"].fillna(config.DEFAULT_FEE_RATE)
    ev = np.where(pol, [betting.ev(p, 1 / o, f) for p, o, f in zip(d["prob"], odds, fee)], d["prob"] * odds - 1)
    kel = np.where(pol, [betting.kelly(p, 1 / o, f) for p, o, f in zip(d["prob"], odds, fee)], [betting.decimal_kelly(p, o) for p, o in zip(d["prob"], odds)])
    if staking == "Fester Betrag":
        stake = np.where(ev > 0, flat, 0.0)
    else:
        frac = 0.25 if staking == "¼ Kelly" else 0.5
        stake = np.minimum(bank * kel * frac, bank * cap)
    stake = np.round(stake, 2)
    res = pd.DataFrame({"Spiel": edited["Spiel"], "Tipp": edited["Tipp"], "EV": ev, "Einsatz": stake, "Erw. Gewinn": stake * ev})
    st.dataframe(res, hide_index=True, width="stretch",
                 column_config={"EV": PCT, "Einsatz": st.column_config.NumberColumn(format="localized"), "Erw. Gewinn": st.column_config.NumberColumn(format="localized")})
    k = st.columns(4)
    k[0].metric("Tipps", int((stake > 0).sum()))
    k[1].metric("Gesamteinsatz", fx.euro(stake.sum(), 2))
    k[2].metric("Erwarteter Gewinn", fx.euro((stake * ev).sum(), 2))
    k[3].metric("Anteil Bankroll", f"{fx.prozent(stake.sum() / bank, 1)}" if bank else "–")

    chosen = edited.index[edited["Übernehmen"]].tolist()
    if st.button(f"➕ {len(chosen)} ausgewählte als Positionen speichern", disabled=not chosen):
        for i in chosen:
            r = d.iloc[i]
            positions.add(
                description=f"{r['match']}: {r['pick']}",
                odds=float(odds.iloc[i]),
                stake=float(stake[i]) or float(flat),
                family=r["family"],
                bookmaker=str(edited["Buchmacher"].iloc[i]),
                condition_id=r["condition_id"],
                outcome_index=int(r["outcome_index"]),
                model_prob=float(r["prob"]),
                event_time=int(r["game_start"]),
            )
        st.success(f"{len(chosen)} Positionen gespeichert.")
    _disclaimer()


# ============================================================ Positionen


def page_positions() -> None:
    st.title("📒 Positionen")
    db.init()
    d = positions.table()
    c = st.columns([3, 1])
    c[0].caption("Mit Polymarket verknüpfte Wetten werden automatisch abgerechnet; dazu wird der Schlusskurs (CLV) geholt.")
    if c[1].button("🔄 Automatisch abrechnen", width="stretch"):
        with st.spinner("Prüfe Ergebnisse …"):
            n = positions.auto_settle()
        st.success(f"{n} Positionen abgerechnet.")
        st.rerun()

    if not d.empty:
        s = positions.stats(d)
        k = st.columns(6)
        k[0].metric("Einsatz (abgerechnet)", fx.euro(s['staked'], 2))
        k[1].metric("Gewinn", fx.euro(s['profit'], 2))
        k[2].metric("Rendite", f"{fx.prozent(s['roi'], 1, vorzeichen=True)}")
        k[3].metric("Trefferquote", f"{fx.prozent(s['hit_rate'], 0)}")
        k[4].metric("Offen", s["open"])
        k[5].metric("Ø CLV", f"{fx.prozent(s['avg_clv'], 1, vorzeichen=True)}" if s["avg_clv"] is not None else "–",
                    help="Closing Line Value: Wie viel besser deine Quote war als die Quote zum Anpfiff. Dauerhaft positiv = echter Vorteil.")

    with st.expander("➕ Wette manuell eintragen (z. B. beim Buchmacher)"):
        with st.form("manual"):
            c = st.columns(3)
            desc = c[0].text_input("Beschreibung", placeholder="Bayern – Leverkusen: Über 2,5")
            family = c[1].selectbox("Sportart", list(FAMILY_DE), format_func=fam)
            book = c[2].text_input("Buchmacher", value="Tipico")
            c = st.columns(3)
            odds = c[0].number_input("Quote (dezimal)", value=2.0, min_value=1.01, step=0.05)
            stake = c[1].number_input("Einsatz (€)", value=10.0, min_value=0.0, step=5.0)
            prob = c[2].number_input("Deine Wahrscheinlichkeit % (optional)", value=0.0, min_value=0.0, max_value=100.0)
            if st.form_submit_button("Speichern") and desc:
                positions.add(desc, odds, stake, family=family, bookmaker=book, model_prob=prob / 100 if prob else None)
                st.success("Gespeichert.")
                st.rerun()

    if d.empty:
        st.info("Noch keine Positionen. Speichere Tipps aus 🎯 Prognosen oder 🧮 Strategie, oder trage eine Wette manuell ein.")
        return

    tab1, tab2 = st.tabs(["Offen", "Verlauf & Auswertung"])
    with tab1:
        o = d[d["status"] == "open"].reset_index(drop=True)
        if o.empty:
            st.write("Keine offenen Positionen.")
        else:
            ev = st.dataframe(
                o[["id", "created_dt", "description", "bookmaker", "odds", "stake", "model_prob", "ev_at_bet"]].rename(
                    columns={"created_dt": "Erstellt", "description": "Wette", "bookmaker": "Wo", "odds": "Quote", "stake": "Einsatz", "model_prob": "Modell", "ev_at_bet": "EV"}
                ),
                hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row", key="open_pos",
                column_config={"Modell": PCT, "EV": PCT, "Erstellt": st.column_config.DatetimeColumn(format="DD.MM. HH:mm"), "id": None},
            )
            sel = [int(o.iloc[i]["id"]) for i in ev.selection.rows]
            c = st.columns(4)
            for col, (lab, status) in zip(c, [("✅ Gewonnen", "won"), ("❌ Verloren", "lost"), ("↩️ Ungültig", "void"), ("🗑️ Löschen", None)]):
                if col.button(lab, disabled=not sel, width="stretch"):
                    for pid in sel:
                        positions.delete(pid) if status is None else positions.settle(pid, status)
                    st.rerun()
    with tab2:
        h = d[d["status"] != "open"].sort_values("settled_ts")
        if h.empty:
            st.write("Noch nichts abgerechnet.")
        else:
            h = h.assign(Datum=pd.to_datetime(h["settled_ts"], unit="s"), Kumuliert=h["profit"].cumsum())
            st.line_chart(h, x="Datum", y="Kumuliert", height=260)
            per = h.groupby("family").agg(Wetten=("id", "count"), Einsatz=("stake", "sum"), Gewinn=("profit", "sum"),
                                          Trefferquote=("status", lambda s: (s == "won").mean()), CLV=("clv", "mean")).reset_index()
            per["Rendite"] = per["Gewinn"] / per["Einsatz"]
            per["Sportart"] = per["family"].map(fam)
            st.dataframe(per[["Sportart", "Wetten", "Einsatz", "Gewinn", "Rendite", "Trefferquote", "CLV"]], hide_index=True, width="stretch",
                         column_config={"Rendite": PCT, "Trefferquote": PCT, "CLV": PCT, "Gewinn": st.column_config.NumberColumn(format="localized")})
            st.dataframe(
                h.sort_values("settled_ts", ascending=False)[["Datum", "description", "bookmaker", "odds", "stake", "status", "profit", "clv"]].rename(
                    columns={"description": "Wette", "bookmaker": "Wo", "odds": "Quote", "stake": "Einsatz", "status": "Ergebnis", "profit": "Gewinn", "clv": "CLV"}
                ),
                hide_index=True, width="stretch",
                column_config={"CLV": PCT, "Gewinn": st.column_config.NumberColumn(format="localized"), "Datum": st.column_config.DatetimeColumn(format="DD.MM.YY")},
            )


# ============================================================ Backtest & Modelle


def page_backtest() -> None:
    st.title("📊 Backtest & Modelle")
    st.markdown(
        "Hier siehst du, ob die Modelle **wirklich** etwas taugen. Für vergangene Spiele wird die Prognose ohne Kenntnis des "
        "Ergebnisses berechnet und mit dem **Polymarket-Kurs beim Anpfiff** verglichen. **Log-Loss** misst die Prognosegüte "
        "(kleiner ist besser). Ist das Modell schlechter als der Markt, sind seine „Edges“ in dieser Sportart meist Illusion."
    )
    if not has_data():
        no_data_hint()
        return
    tab1, tab2 = st.tabs(["Backtest", "Modelle & Ratings"])
    with tab1:
        c = st.columns([3, 1, 1])
        pools = [p for p, fm in db.df("SELECT pool, MIN(family) fam, COUNT(*) n FROM games GROUP BY pool HAVING n >= 100 ORDER BY n DESC")[["pool", "fam"]].itertuples(index=False) if not excluded(fm)]
        sel = c[0].multiselect("Pools (leer = alle)", pools, placeholder="Alle")
        n = c[1].number_input("Spiele je Pool", value=300, step=100, min_value=50)
        if c[2].button("▶️ Backtest starten", width="stretch"):
            run_with_status("Backtest läuft (lädt Schlusskurse, kann einige Minuten dauern)", backtest.run_all, n_eval=int(n), pools=sel or None)
            st.rerun()
        r = backtest.results()
        if r.empty:
            st.info("Noch kein Backtest gelaufen.")
        else:
            r = r.sort_values("Spiele", ascending=False)
            r["Urteil"] = np.where(
                r["Log-Loss Markt"] > 0.69, "⚠️ Marktpreise unzuverlässig",
                np.where(r["Modell besser?"], "✅ Modell schlägt Markt", "❌ Markt ist besser"),
            )
            cols = ["Pool", "Modell", "Spiele", "Log-Loss Modell", "Log-Loss Markt", "Urteil"] + [x for x in r.columns if x.startswith("Rendite") or x.startswith("Wetten")]
            st.dataframe(r[cols], hide_index=True, width="stretch",
                         column_config={c2: PCT for c2 in cols if c2.startswith("Rendite")} | {"Log-Loss Modell": st.column_config.NumberColumn(format="localized"), "Log-Loss Markt": st.column_config.NumberColumn(format="localized")})
            st.caption("Rendite = simulierte Wetten mit festem Einsatz gegen den Schlusskurs inkl. Gebühr, nur wenn Modell und Markt um mehr als die Edge-Schwelle abweichen. "
                       "„Marktpreise unzuverlässig“: Der Markt war schlechter als ein Münzwurf, meist wegen veralteter Preise in dünnen Märkten. "
                       "Ergebnisse dort nicht vertrauen.")
            pick = st.selectbox("Details", r["Pool"].tolist())
            det = backtest.detail(pick)
            if det and det.get("calibration"):
                cal = pd.DataFrame(det["calibration"])
                base = alt.Chart(pd.DataFrame({"x": [0, 1], "y": [0, 1]})).mark_line(strokeDash=[4, 4], color="gray").encode(x="x", y="y")
                pts = alt.Chart(cal).mark_circle(size=90).encode(
                    x=alt.X("model:Q", title="Modell-Wahrscheinlichkeit", scale=alt.Scale(domain=[0, 1])),
                    y=alt.Y("actual:Q", title="Tatsächliche Trefferquote", scale=alt.Scale(domain=[0, 1])),
                    size=alt.Size("n:Q", legend=None),
                    tooltip=["bin", "model", "actual", "n"],
                )
                st.altair_chart(base + pts, width="stretch")
                st.caption("Kalibrierung: Punkte nahe der Diagonale = das Modell ist ehrlich kalibriert.")
    with tab2:
        models = modeling.load_models()
        if not models:
            st.info("Noch keine Modelle trainiert.")
            return
        st.dataframe(modeling.summary(models), hide_index=True, width="stretch")
        pool = st.selectbox("Ratings anzeigen", sorted(models), index=sorted(models).index("soccer") if "soccer" in models else 0)
        q = st.text_input("Suche Team/Spieler")
        t = modeling.ratings_table(models[pool], top=100000)
        if q:
            t = t[t["Name"].str.contains(q, case=False, na=False)]
        st.dataframe(t.head(500), hide_index=True, width="stretch")


# ============================================================ Verletzungen


def page_injuries() -> None:
    st.title("🩹 Verletzungen & News")
    st.markdown(
        "Verletzungslisten von ESPN für **NBA, WNBA, NFL, NHL, MLB**. Bei Basketball und Football wird geschätzt, "
        "wie viele Punkte ein Ausfall kostet; das fließt direkt in die Prognosen ein. Langzeitausfälle und Ausfälle, "
        "die älter als 21 Tage sind, zählen nicht: Sie stecken schon in der aktuellen Form des Teams.\n\n"
        "Für **alle anderen Sportarten** (Fußball, Tennis, Esports …) gibt es den **📰 News-Alarm**: Fällt der "
        "Polymarket-Preis eines Tipps in 24 h um mehr als 8 Prozentpunkte, weiß der Markt vermutlich etwas "
        "(Verletzung, Aufstellung, Rotation). Solche Tipps werden standardmäßig ausgeblendet."
    )
    c = st.columns([3, 1])
    c[0].write(f"Letzte Aktualisierung: **{ago(db.kv_get('last_injuries'))}**")
    if c[1].button("🔄 Verletzungen laden", width="stretch"):
        run_with_status("Lade Verletzungen", injuries.refresh)
        st.rerun()
    d = injuries.summary_for_ui()
    if d.empty:
        st.info("Noch keine Verletzungsdaten.")
        return
    leagues = st.pills("Liga", sorted(d["league"].unique()), selection_mode="multi", format_func=str.upper)
    if leagues:
        d = d[d["league"].isin(leagues)]
    only = st.toggle("Nur Ausfälle mit Einfluss aufs Modell", value=True)
    if only:
        d = d[d["Abzug"] > 0]
    st.dataframe(
        d[["league", "team", "player", "position", "status", "Datum", "ppg", "Abzug", "comment"]].rename(
            columns={"league": "Liga", "team": "Team", "player": "Spieler", "position": "Pos.", "status": "Status",
                     "ppg": "Punkte/Spiel", "Abzug": "Punkte-Abzug Team", "comment": "Meldung"}
        ),
        hide_index=True, width="stretch",
        column_config={"Datum": st.column_config.DatetimeColumn(format="DD.MM."), "Liga": st.column_config.TextColumn(),
                       "Punkte-Abzug Team": st.column_config.NumberColumn(format="localized"), "Punkte/Spiel": st.column_config.NumberColumn(format="localized")},
    )
    st.subheader("📰 Aktuelle News-Alarme (alle Sportarten)")
    e = load_edges()
    if not e.empty:
        n = e[e["news_alert"] & ~e["suspicious"]].sort_values("move24").head(40)
        st.dataframe(
            pd.DataFrame({"Sportart": n["Sportart"], "Spiel": n["match"], "Tipp": n["pick"], "Modell": n["prob"],
                          "Preis jetzt": n["price"], "Bewegung 24h": n["move24"], "Link": n["Link"]}),
            hide_index=True, width="stretch",
            column_config={"Modell": PCT, "Bewegung 24h": st.column_config.NumberColumn(format="localized"),
                           "Link": st.column_config.LinkColumn(display_text="Quoten vergleichen", help="OddsPortal: Quoten deutscher Anbieter (Tipico, bet365, Betano, Oddset …)")},
        )


# ============================================================ Daten / Einstellungen


def page_data() -> None:
    st.title("⚙️ Daten & Einstellungen")
    db.init()
    first = not has_data()
    if first:
        st.warning("Noch keine Daten vorhanden.")
    c = st.columns(3)
    days = c[0].number_input("Tage Historie", value=config.BACKFILL_DAYS, min_value=14, max_value=730, step=30,
                             help="Mehr Historie = bessere Modelle. 180 Tage dauern beim ersten Mal ca. 5–10 Minuten.")
    if c[1].button("🚀 Ersteinrichtung starten" if first else "📥 Historie nachladen", width="stretch"):
        def job(progress):
            ingest.setup(progress)
            ingest.backfill(int(days), progress=progress)
            _update_job(progress)
        run_with_status("Ersteinrichtung", job)
        st.rerun()
    if c[2].button("🔄 Märkte & Prognosen aktualisieren", width="stretch", disabled=first):
        run_with_status("Aktualisieren", _update_job)
        st.rerun()

    if not first:
        s = db.df("SELECT COUNT(*) games, MIN(start_ts) first, MAX(start_ts) last, COUNT(DISTINCT code) leagues FROM games").iloc[0]
        k = st.columns(4)
        k[0].metric("Spiele", f"{int(s['games']):,}".replace(",", "."))
        k[1].metric("Ligen", int(s["leagues"]))
        k[2].metric("Von", str(pd.to_datetime(s["first"], unit="s").date()))
        k[3].metric("Bis", str(pd.to_datetime(s["last"], unit="s").date()))
        st.write(
            f"Ergebnisse {ago(db.kv_get('last_results_update'))} · offene Märkte {ago(db.kv_get('last_open_update'))} · "
            f"Modelle {ago(db.kv_get('last_train'))} · Prognosen {ago(db.kv_get('last_predict'))} · Whales {ago(db.kv_get('last_whale_poll'))}"
        )
        per = db.df("SELECT family, COUNT(*) n, COUNT(DISTINCT code) ligen FROM games GROUP BY family ORDER BY n DESC")
        per["Sportart"] = per["family"].map(fam)
        st.dataframe(per[["Sportart", "n", "ligen"]].rename(columns={"n": "Spiele", "ligen": "Ligen"}), hide_index=True)

    st.subheader("Automatisch im Hintergrund")
    st.markdown(
        "Für Echtzeit-Whales und frische Prognosen in einem zweiten Terminal laufen lassen:\n"
        "```\npython -m edgelab run\n```\n"
        "Whales werden alle 2 Minuten geprüft, Märkte und Prognosen alle 15 Minuten aktualisiert."
    )
    st.subheader("Telegram-Benachrichtigungen")
    if notify.enabled():
        st.success(f"Aktiv – Meldung bei Profi-Wetten ab {fx.euro(fx.to_eur(config.WHALE_NOTIFY_USD))}.")
        if st.button("Testnachricht senden"):
            st.write("Gesendet ✅" if notify.send("EdgeLab-Test ✅") else "Fehlgeschlagen ❌")
    else:
        st.info("Nicht eingerichtet. Anleitung im README (Bot bei @BotFather anlegen, Token + Chat-ID in die Datei `.env`).")
