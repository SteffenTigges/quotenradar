"""Kommandozeile.

    python -m edgelab setup              # Datenbank + Ligen anlegen, Ergebnisse laden (erster Start)
    python -m edgelab update             # offene Märkte, Modelle, Prognosen aktualisieren
    python -m edgelab whales             # einmal nach großen Wetten suchen
    python -m edgelab backtest           # Modelle gegen Polymarket-Schlusskurse testen
    python -m edgelab run                # Dauerbetrieb: alles automatisch im Hintergrund
"""
from __future__ import annotations

import argparse
import logging
import time
import traceback

from . import backtest, bookmakers, darts, db, ingest, injuries, modeling, positions, predict, props, whales


def say(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def cmd_setup(args) -> None:
    ingest.setup(say)
    say(f"Lade Ergebnisse der letzten {args.days} Tage (einmalig, dauert ein paar Minuten) …")
    n = ingest.backfill(args.days, progress=say)
    say(f"{n} Spiele geladen.")
    cmd_update(args)


def cmd_update(args=None) -> None:
    db.init()
    if not db.kv_get("backfill_days"):
        say("Noch keine Ergebnisse – bitte zuerst `python -m edgelab setup` ausführen.")
        return
    say("Neue Ergebnisse …")
    ingest.backfill(days=3, progress=lambda s: None)
    ingest.refresh_stale()
    say("Offene Märkte …")
    n = ingest.refresh_open(progress=lambda s: None)
    say(f"{n} offene Märkte.")
    try:
        injuries.refresh(progress=say)
    except Exception as e:  # noqa: BLE001
        say(f"Verletzungen nicht geladen: {e}")
    try:
        bookmakers.refresh(progress=say)
    except Exception as e:  # noqa: BLE001
        say(f"Quotenvergleich nicht geladen: {e}")
    try:
        names = db.df("SELECT outcome0, outcome1 FROM markets m JOIN sports s USING(code) WHERE s.family='darts' AND m.closed=0 "
                      "AND m.market_type='moneyline' AND m.game_start > strftime('%s','now')")
        darts.fetch(sorted(set(names["outcome0"]) | set(names["outcome1"])), progress=say)
    except Exception as e:  # noqa: BLE001
        say(f"Darts-Statistiken nicht geladen: {e}")
    try:
        props.refresh(progress=say)
    except Exception as e:  # noqa: BLE001
        say(f"NBA-Spielerdaten nicht geladen: {e}")
    models = modeling.train_all(progress=lambda s: None)
    say(f"{len(models)} Modelle trainiert.")
    predict.run(models, progress=say)
    k = positions.auto_settle()
    if k:
        say(f"{k} Positionen abgerechnet.")


def cmd_whales(args) -> None:
    db.init()
    whales.poll(min_usd=args.min_usd, progress=say)


def cmd_backtest(args) -> None:
    db.init()
    backtest.run_all(n_eval=args.n, pools=args.pool or None, progress=say)


def cmd_export(args) -> None:
    db.init()
    from . import export

    export.run(args.out, hours=args.hours, progress=say)


def cmd_ci(args) -> None:
    """Ein kompletter Durchlauf für GitHub Actions: beim ersten Mal Historie laden, sonst aktualisieren,
    Großwetten abrufen, einmal täglich den Backtest rechnen und data.json für die Web-App schreiben."""
    db.init()
    from . import config, export

    if not db.kv_get("backfill_days"):
        say(f"Erster Lauf: lade {config.BACKFILL_DAYS} Tage Historie …")
        ingest.setup(say)
        ingest.backfill(config.BACKFILL_DAYS, progress=say)
    cmd_update()
    try:
        whales.poll(progress=say)
    except Exception as e:  # noqa: BLE001
        say(f"Großwetten nicht geladen: {e}")
    if db.now() - db.kv_get("last_backtest", 0) > 20 * 3600:
        try:
            backtest.run_all(n_eval=300, progress=say)
            db.kv_set("last_backtest", db.now())
        except Exception as e:  # noqa: BLE001
            say(f"Backtest übersprungen: {e}")
    export.run(args.out, hours=args.hours, progress=say)


def cmd_run(args) -> None:
    """Endlosschleife: Whales alle 2 Min., Märkte/Prognosen alle 15 Min."""
    db.init()
    last_update = 0.0
    say("Dauerbetrieb gestartet (Strg+C zum Beenden).")
    while True:
        try:
            if time.time() - last_update > args.update_minutes * 60:
                cmd_update()
                last_update = time.time()
            whales.poll(progress=say)
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001
            say("Fehler (wird beim nächsten Durchlauf erneut versucht):\n" + traceback.format_exc(limit=3))
        time.sleep(args.whale_seconds)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="edgelab", description="EdgeLab – Sportwetten-Analyse mit Polymarket-Daten")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="Erster Start: Ligen + historische Ergebnisse laden")
    s.add_argument("--days", type=int, default=None, help="Tage Historie (Standard 180)")
    s.set_defaults(func=cmd_setup)

    s = sub.add_parser("update", help="Märkte, Modelle und Prognosen aktualisieren")
    s.set_defaults(func=cmd_update)

    s = sub.add_parser("whales", help="Große Wetten abrufen")
    s.add_argument("--min-usd", type=float, default=None)
    s.set_defaults(func=cmd_whales)

    s = sub.add_parser("backtest", help="Modelle gegen Schlusskurse testen")
    s.add_argument("--n", type=int, default=300, help="Spiele pro Pool")
    s.add_argument("--pool", action="append", help="Nur diesen Pool (mehrfach möglich)")
    s.set_defaults(func=cmd_backtest)

    s = sub.add_parser("export", help="data.json für die Web-App schreiben")
    s.add_argument("--out", default="../web/data.json")
    s.add_argument("--hours", type=int, default=48)
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("ci", help="Kompletter Durchlauf für GitHub Actions (aktualisieren + exportieren)")
    s.add_argument("--out", default="../web/data.json")
    s.add_argument("--hours", type=int, default=48)
    s.set_defaults(func=cmd_ci)

    s = sub.add_parser("run", help="Dauerbetrieb")
    s.add_argument("--update-minutes", type=float, default=15)
    s.add_argument("--whale-seconds", type=float, default=120)
    s.set_defaults(func=cmd_run)

    args = p.parse_args(argv)
    if getattr(args, "days", 0) is None:
        from . import config

        args.days = config.BACKFILL_DAYS
    args.func(args)


if __name__ == "__main__":
    main()
