"""EdgeLab-Dashboard. Start: streamlit run app.py"""
import streamlit as st

from edgelab import db
from edgelab.ui import pages

st.set_page_config(page_title="EdgeLab", page_icon="📈", layout="wide")
db.init()

nav = st.navigation(
    {
        "": [
            st.Page(pages.page_overview, title="Übersicht", icon="🏠", url_path="overview", default=True),
            st.Page(pages.page_games, title="Spiele & Quoten", icon="🏟️", url_path="games"),
            st.Page(pages.page_whales, title="Großwetten", icon="🐋", url_path="whales"),
            st.Page(pages.page_predictions, title="Value-Tipps", icon="🎯", url_path="predictions"),
            st.Page(pages.page_positions, title="Meine Wetten", icon="📒", url_path="positions"),
        ],
        "Mehr": [
            st.Page(pages.page_strategy, title="Strategie", icon="🧮", url_path="strategy"),
            st.Page(pages.page_injuries, title="Verletzungen & News", icon="🩹", url_path="injuries"),
            st.Page(pages.page_backtest, title="Backtest & Modelle", icon="📊", url_path="backtest"),
            st.Page(pages.page_data, title="Daten & Einstellungen", icon="⚙️", url_path="data"),
        ],
    }
)
nav.run()
