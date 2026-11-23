# Quotenradar

Persönliche Sportwetten-Analyse: Wer gewinnt mit welcher Wahrscheinlichkeit, was wäre eine faire Quote, und zahlt ein deutscher Anbieter mehr?

| Ordner | Inhalt |
|---|---|
| `web/` | Die App (Webseite für Tablet, Handy und PC) |
| `edgelab/` | Daten-Pipeline und Modelle in Python (interner Paketname `edgelab`), dazu ein lokales Dashboard ([Anleitung](edgelab/README.md)) |
| `.github/workflows/app.yml` | Lädt jede Stunde neue Daten und veröffentlicht die App |

## So läuft die App über GitHub

GitHub Actions startet jede Stunde `python -m edgelab ci`. Der Lauf lädt Spiele, Ergebnisse, Verletzungen, Darts-Statistiken, Großwetten und Quoten, rechnet die Modelle und schreibt `web/data.json`. Danach veröffentlicht GitHub Pages den Ordner `web/` als Webseite. Die Datenbank bleibt im GitHub-Cache, damit nicht jedes Mal die ganze Historie neu geladen wird.

### Einmalig einrichten

1. **Repository öffentlich halten.** GitHub Pages und die Actions-Minuten sind für öffentliche Repositories kostenlos.
2. **GitHub Pages einschalten.** Settings → Pages → Source: **GitHub Actions**.
3. **Auf `main` bringen.** Der Workflow läuft nur auf dem Standard-Branch. Sobald der Code auf `main` liegt, startet der erste Lauf automatisch. Er dauert beim ersten Mal 10 bis 15 Minuten, danach etwa 5 Minuten pro Stunde.
4. **Optional: Quoten von Tipico, Betano und Winamax.**
   1. Auf the-odds-api.com den kostenlosen Plan „Starter“ wählen und mit E-Mail-Adresse anmelden. Der Schlüssel kommt per E-Mail, eine lange Zeichenfolge aus Buchstaben und Zahlen.
   2. Im Repository: Settings → Secrets and variables → Actions → New repository secret.
   3. Name: `ODDS_API_KEY`, Secret: den Schlüssel einfügen, Add secret.
   4. Actions → „Quotenradar aktualisieren“ → Run workflow. Im Protokoll steht danach „Quotenvergleich: … Quoten geladen, noch … Abfragen diesen Monat“.

   Die Quoten werden einmal am Tag für 11 große Ligen geladen (Bundesliga, 2. Bundesliga, Premier League, LaLiga, Serie A, Ligue 1, Champions League, Europa League, NBA, NFL, NHL). Das sind rund 330 der 500 kostenlosen Abfragen pro Monat.

Die Adresse der App steht danach unter Settings → Pages, hier: `https://steffentigges.github.io/quotenradar/`. Am Tablet im Browser öffnen und „Zum Home-Bildschirm hinzufügen“ wählen, dann startet Quotenradar wie eine App.

Manuell neu laden: Actions → „Quotenradar aktualisieren“ → Run workflow.

### Lokal ansehen

```bash
cd edgelab
pip install -r requirements.txt
python -m edgelab ci --out ../web/data.json
cd ../web && python -m http.server 8000   # dann http://localhost:8000 öffnen
```

Nur zur Information, keine Wettberatung. Wetten kann süchtig machen. Hilfe: 0800 1 37 27 00, check-dein-spiel.de.
