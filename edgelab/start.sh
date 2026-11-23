#!/usr/bin/env bash
# Startet Hintergrund-Dienst + Dashboard. Beenden mit Strg+C.
cd "$(dirname "$0")"
[ -d .venv ] && source .venv/bin/activate
python -m edgelab run &
WORKER=$!
trap 'kill $WORKER 2>/dev/null' EXIT
streamlit run app.py
