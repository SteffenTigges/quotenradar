@echo off
REM Startet Hintergrund-Dienst + Dashboard
cd /d "%~dp0"
if exist .venv\Scripts\activate.bat call .venv\Scripts\activate.bat
start "EdgeLab Dienst" cmd /k python -m edgelab run
streamlit run app.py
