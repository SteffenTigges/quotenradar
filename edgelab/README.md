# 📈 EdgeLab

Persönliches Sportwetten-Analyse-Tool nach dem Vorbild von oddify.ai, komplett **kostenlos** und **lokal auf deinem Rechner**.

| Funktion | Was sie macht |
|---|---|
| 🏟️ **Spiele** | Pro Partie: wer gewinnt mit wie viel Prozent (Modell vs. Markt) und ein Wettangebot wie bei Tipico/Betano/Winamax mit fairen Quoten: 1X2, Doppelte Chance, Unentschieden keine Wette, Über/Unter, Beide treffen, Handicap, genaues Ergebnis, Zu-Null-Sieg, Satz-/Map-Wetten, Siegabstand, **NBA-Spielerwetten** (Punkte, Rebounds, Assists, Dreier, P+R+A mit Linie = Saisonschnitt mit ,5) |
| 🎯 **Value-Tipps** | Modell-Wahrscheinlichkeiten für **alle Sportarten auf Polymarket** (~500 Ligen: Fußball, NBA, NFL, NHL, Tennis, Darts, Tischtennis, Cricket … – Esports standardmäßig ausgeblendet) mit EV und Kelly-Einsatz |
| 🐋 **Whale Tracker** | Große Sportwetten in Echtzeit, Wallets eingeordnet (Sharp / Neutral / Frische Wallet / Dumb Money), Abgleich mit dem Modell, Ranglisten je Sportart, optional Telegram-Alarm |
| 🧮 **Strategie** | Filter nach EV, Edge, Sportart und Preis; Kelly- oder Flat-Einsatz; eigene Buchmacher-Quoten direkt in der Tabelle eintragen |
| 📒 **Positionen** | Wetten erfassen, automatische Abrechnung, ROI, Trefferquote, **CLV** (Closing Line Value) je Sportart |
| 🩹 **Verletzungen & News** | Frische Ausfälle (NBA, WNBA, NFL, NHL, MLB) fließen in die Prognosen ein; News-Alarm für alle Sportarten, wenn sich der Markt stark gegen einen Tipp bewegt |
| 📊 **Backtest** | Prüft ehrlich, ob die Modelle die Polymarket-Schlusskurse schlagen, mit Kalibrierung und simuliertem ROI |

## Woher kommen die Daten?

Alles kommt aus den **öffentlichen Polymarket-APIs**. Es gibt keinen API-Key, kein Konto und keine Kosten:

- **Ergebnisse und Spielstände**: aufgelöste Spielmärkte (~1.300 Spiele pro Tag über alle Sportarten)
- **Quoten**: aktuelle Kauf- und Verkaufspreise jedes Markts (Preis 0,40 = 40 % = Quote 2,50)
- **Schlusskurse**: Preis beim Anpfiff, der Maßstab für Backtest und CLV
- **Whales**: alle großen Trades, Wallet-Statistiken und Ranglisten

Weil Ergebnisse und Quoten aus derselben Quelle stammen, passen die Team- und Spielernamen immer zusammen.

## Die Modelle

| Modell | Für | Wie |
|---|---|---|
| **Elo** | alle Sportarten | Rating pro Team/Spieler, K-Faktor und Heimvorteil werden pro Sportart automatisch optimiert, bei Punktsportarten mit Siegabstand |
| **Poisson + Dixon-Coles** | Fußball, Eishockey, Baseball | Angriffs-/Abwehrstärke je Team mit Zeitgewichtung, daraus Sieg/Unentschieden/Niederlage, Über/Unter, beide treffen, Handicap und Team-Tore |
| **Punktemodell** | Basketball, American Football | Ridge-Regression je Liga, Normalverteilung für Spread, Total und Sieg |

Bei Sieger-Märkten werden Elo und Tor-/Punktemodell gemittelt.

> ⚠️ **Ehrlich gesagt:** Polymarket-Preise sind meist sehr gut. Im Backtest ist der Markt in den meisten Sportarten besser als diese einfachen Modelle. Ein „positiver EV“ heißt erst mal nur, dass Modell und Markt sich nicht einig sind. Schau im **Backtest**, wo ein Modell überhaupt etwas taugt. Dein bester Erfolgsmesser ist ein dauerhaft positiver **CLV** im Positions-Tracker.

## Verletzungen und News

| Quelle | Sportarten | Wirkung |
|---|---|---|
| ESPN-Verletzungslisten (kostenlos, ohne Key) | NBA, WNBA, NFL, NHL, MLB | Basketball: Punkte-Abzug aus der Saisonstatistik des Spielers. Football: Abzug nach Position (Quarterback ≈ 5,5 Punkte). Verschiebt Punktemodell und Elo. NHL/MLB: nur Anzeige |
| News-Alarm (Polymarket-Preisbewegung) | alle | Fällt der Preis eines Tipps in 24 h um mehr als 8 Prozentpunkte, wird der Tipp markiert und standardmäßig ausgeblendet |

Gezählt werden nur frische Ausfälle (jünger als 21 Tage, kein „Injured Reserve“). Langzeitausfälle stecken schon in der aktuellen Form des Teams.

Mögliche Erweiterungen: API-Football (kostenloser Key, 100 Abfragen/Tag) für Verletzungen, Sperren und Aufstellungen im Fußball; Liquipedia für Kaderwechsel im Esports.

## Installation (einmalig)

Voraussetzung: **Python 3.10 oder neuer** ([python.org](https://www.python.org/downloads/), unter Windows bei der Installation „Add Python to PATH“ anhaken).

```bash
cd edgelab
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
python -m edgelab setup        # lädt 180 Tage Ergebnisse, dauert ca. 5–10 Minuten
```

## Starten

**Windows:** Doppelklick auf `start.bat`. **Mac/Linux:** `./start.sh`.

Beide Skripte starten zwei Dinge:
1. das **Dashboard** im Browser (http://localhost:8501)
2. den **Hintergrund-Dienst**: Whales alle 2 Minuten, Märkte und Prognosen alle 15 Minuten

Manuell geht es so:

```bash
streamlit run app.py           # Dashboard
python -m edgelab run          # Hintergrund-Dienst (zweites Terminal)
```

## Weitere Befehle

```bash
python -m edgelab update       # Märkte, Modelle, Prognosen einmal aktualisieren
python -m edgelab whales       # einmal nach großen Wetten suchen
python -m edgelab backtest     # alle Modelle gegen Schlusskurse testen (dauert ein paar Minuten)
python -m edgelab backtest --pool soccer --n 1000
```

## Wetten aus Deutschland

Polymarket dient nur als **Datenquelle** (Ergebnisse, Marktpreise, Whales); wetten kann man dort aus Deutschland nicht.
Alle Links im Dashboard führen deshalb zum Quotenvergleich von **OddsPortal**, der die Quoten deutscher Anbieter
(Tipico, bet365, Betano, Oddset …) für das Spiel zeigt. Liegt die Quote eines Anbieters über der **fairen Quote**
aus dem Modell, ist der Tipp laut Modell Value. Eigene Quoten kannst du in **Strategie** eintragen, EV und Einsatz rechnen sich neu.

## Deutsch und in Euro

Die ganze Oberfläche ist deutsch. Polymarket rechnet in US-Dollar; alle Beträge (Großwetten, Liquidität, Ranglisten)
werden mit dem täglichen **Referenzkurs der Europäischen Zentralbank** in Euro umgerechnet. Zahlen, Prozente und Quoten
erscheinen im deutschen Format (1.234,56 €, 52,6 %, Quote 1,90), Zeiten in deutscher Zeit.

## Darts

Für Darts nutzt EdgeLab die Spielerstatistiken von **Darts Orakel** (dartsorakel.com, öffentliche Spielerprofile,
einmal täglich): 3-Dart-Average, First 9, 180er, Checkout-Quote und gewonnene Legs der letzten 12 Monate.
Aus dem Average-Unterschied ergibt sich die Chance pro Leg und mit dem Spielmodus (z. B. Grand Slam ab Viertelfinale:
Best of 31 Legs) das Wettangebot wie bei deutschen Buchmachern: **Sieger, genaues Ergebnis, Handicap Legs,
Gesamtzahl Legs**. Turniere ohne hinterlegten Modus bekommen nur die Siegerwette (Modi in `edgelab/darts.py`, `FORMATS`).

## Quotenvergleich Tipico · Betano · Winamax

Unter jedem Spiel (und im Whale Tracker) vergleicht EdgeLab die Quoten von **Tipico**, **Betano** und **Winamax** mit der
fairen Quote des Modells. Die Quoten kommen von **The Odds API** (kostenlos, 500 Abfragen pro Monat):

1. Auf https://the-odds-api.com kostenlos registrieren und den API-Key kopieren.
2. In die Datei `.env` im Ordner `edgelab/` eintragen: `ODDS_API_KEY=dein_schlüssel`
3. Fertig: Die Quoten werden einmal täglich für Bundesliga, 2. Bundesliga, Premier League, LaLiga, Serie A, Ligue 1,
   Champions League, Europa League, NBA, NFL und NHL geladen (ca. 22 Abfragen pro Tag mit Sieger + Über/Unter).
   Andere Auswahl: `ODDS_API_SPORTS=soccer_germany_bundesliga,basketball_nba`

Betano kommt dabei aus Betanos britischem Angebot (die API führt Betano.de nicht); die Quoten sind meist gleich oder sehr nah dran.
**Value** = beste Quote × Modellwahrscheinlichkeit − 1. Liegen Modell und Anbieter extrem weit auseinander, zeigt die App
„Modell unsicher“ statt Value, weil dem Modell dann meist eine Information fehlt (Verletzung, Aufstellung).

## Einstellungen (optional)

Lege eine Datei `.env` im Ordner `edgelab/` an:

```ini
EDGELAB_BACKFILL_DAYS=180        # Tage Historie beim Setup
EDGELAB_EXCLUDE=esports          # ausgeblendete Sportarten (kommagetrennt), z. B. esports,table-tennis
EDGELAB_LOOKAHEAD_HOURS=72       # wie weit voraus Spiele geladen werden
EDGELAB_WHALE_MIN_USD=2000       # ab welchem Betrag eine Wette als Whale gilt
EDGELAB_WHALE_NOTIFY_USD=5000    # ab welchem Betrag Telegram-Alarm (nur Sharp-Wallets)

# Telegram-Alarm für Sharp-Wetten:
# 1. In Telegram @BotFather anschreiben, /newbot, Token kopieren
# 2. Deinem Bot eine Nachricht schicken, dann https://api.telegram.org/bot<TOKEN>/getUpdates öffnen
#    und die "chat":{"id": ...} kopieren
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```

## Wallet-Typen im Whale Tracker

| Typ | Regel |
|---|---|
| 🟢 **Sharp** | ≥ $20.000 Gewinn in Sportmärkten **und** ≥ 1 % Rendite auf das Volumen |
| 🔴 **Dumb Money** | ≤ −$10.000 in Sportmärkten |
| 🟡 **Frische Wallet** | jünger als 30 Tage oder weniger als 25 Trades |
| ⚪ **Neutral** | alles andere |

Die Daten stammen aus der offiziellen Polymarket-Sport-Rangliste. Die Ranglisten „je Sportart“ entstehen aus deinen eigenen getrackten Trades und werden besser, je länger der Hintergrund-Dienst läuft.

## Projektaufbau

```
edgelab/
├── app.py                 # Dashboard (Streamlit)
├── edgelab/
│   ├── polymarket.py      # API-Client (Gamma + Data API v2)
│   ├── ingest.py          # Ergebnisse, offene Märkte, Schlusskurse laden
│   ├── families.py        # ~500 Ligen -> Sportarten, Titel/Ergebnis parsen
│   ├── models/            # elo.py, goals.py (Poisson), points.py
│   ├── modeling.py        # Training pro Sportart
│   ├── predict.py         # Prognosen, EV, Kelly
│   ├── whales.py          # Whale Tracker
│   ├── injuries.py        # Verletzungen (ESPN) + Punkte-Abzug
│   ├── games.py           # Spielkarten + Buchmacher-Wettangebot
│   ├── props.py           # NBA-Spielerwetten (ESPN-Spiellogs, Negativ-Binomial)
│   ├── positions.py       # Positions-Tracker
│   ├── backtest.py        # Backtest gegen Schlusskurse
│   ├── notify.py          # Telegram
│   └── cli.py             # Kommandozeile
├── tests/                 # pytest
└── data/                  # SQLite-Datenbank (wird angelegt, nicht im Git)
```

Tests: `python -m pytest`

## Grenzen

- Nur Spiele, die es auf Polymarket gibt. Golf- und Rennsport-Märkte (viele Teilnehmer) werden getrackt, aber nicht modelliert.
- Spieler-Props (Punkte, Rebounds …) und Ecken werden nicht bepreist.
- Verletzungen kennt das Modell nur für NBA, WNBA, NFL (und als Anzeige NHL/MLB). Aufstellungen und Fußball-Ausfälle kennt es nicht; dort hilft nur der News-Alarm.
- Die Daten sind öffentliche Polymarket-Daten. Polymarket selbst ist nicht in jedem Land für den Handel zugelassen; die Daten lesen kannst du überall.

**Nur zur Information und Unterhaltung, keine Wett- oder Finanzberatung.** Wetten kann süchtig machen. Hilfe gibt es bei [check-dein-spiel.de](https://www.check-dein-spiel.de) und unter 0800 1 37 27 00 (kostenlos).
