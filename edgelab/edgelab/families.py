"""Zuordnung der ~500 Polymarket-Ligen zu Sportarten (Familien) und Rating-Pools, plus Titel-Parsing."""
from __future__ import annotations

import re

# Polymarket-Tag-IDs, die eine Sportart kennzeichnen.
FAMILY_TAGS = {
    "100350": "soccer",
    "517": "cricket",
    "28": "basketball",
    "102883": "volleyball",
    "103767": "table-tennis",
    "105715": "table-tennis",
    "64": "esports",
    "100088": "hockey",
    "102193": "rugby",
    "678": "baseball",
    "102897": "handball",
    "864": "tennis",
    "100219": "golf",
    "102393": "lacrosse",
    "434": "racing",
    "102166": "racing",
    "102471": "pickleball",
    "101437": "darts",
    "1186": "american-football",
    "683": "boxing",
}

FAMILY_CODES = {
    "american-football": {"nfl", "cfb", "ufl", "cfl"},
    "mma": {"ufc", "pfl", "bellator"},
    "boxing": {"zuffa", "boxing", "powerslap"},
    "tennis": {"atp", "wta", "itf", "atc", "wimbledon", "daviscup", "bjkcup", "atp-doubles", "wta-doubles"},
    "racing": {"f1", "motogp", "moto2", "moto3", "nascar", "nascar-truck", "nascar-xfinity", "indycar", "formulae", "wsbk"},
    "esports": {"cs2", "lol", "val", "dota2", "ow", "owq", "r6siege", "codmw", "pubg", "rl", "rlchallenge", "sc2", "mlbb", "lol-wild-rift", "lec", "lpl"},
    "basketball": {"nba", "wnba", "ncaab", "cwbb", "euroleague", "cbb", "kbl", "nbl", "lnb", "pro-a", "abb"},
    "hockey": {"nhl", "ahl", "khl", "shl", "liiga", "dehl", "cehl", "snhl", "hockeychl", "ncaahockey"},
    "baseball": {"mlb", "kbo", "npb", "cpbl", "ncaabaseball", "lidom", "lmb", "lmp", "lvbp", "wbc"},
    "aussie-rules": {"afl", "aflw"},
    "chess": {"chess"},
    "darts": {"darts", "pdcdarts"},
    "golf": {"pga", "lpga", "liv", "dpwt"},
}
CODE_PREFIXES = [
    ("bk", "basketball"),
    ("hb", "handball"),
    ("vb", "volleyball"),
    ("cric", "cricket"),
    ("cr", "cricket"),
    ("tt", "table-tennis"),
    ("setka", "table-tennis"),
    ("ru", "rugby"),
    ("rl", "rugby"),
]

# Sportarten ohne Heimvorteil (Einzelsport, neutrale Orte).
NEUTRAL_FAMILIES = {
    "tennis", "table-tennis", "esports", "mma", "boxing", "darts", "chess", "pickleball", "golf", "racing",
}
# Familien, in denen das Ergebnis eine echte Tor-/Punktzahl ist (für Tor- und Punktemodelle).
SCORE_FAMILIES = {
    "soccer", "basketball", "hockey", "american-football", "baseball", "handball", "rugby",
    "aussie-rules", "lacrosse",
}
WOMEN_CODES = {"wnba", "cwbb", "wsl", "wsl2", "nwsl", "uwcl", "fifaw", "aflw", "weuc", "weuq", "wwcquefa", "u20wwc"}


def classify(code: str, name: str, tag_ids: str) -> tuple[str, str]:
    """Gibt (family, pool) zurück. Pool = Gruppe, deren Teams/Spieler gegeneinander antreten können."""
    tags = [t.strip() for t in (tag_ids or "").split(",") if t.strip()]
    family = None
    for fam, codes in FAMILY_CODES.items():
        if code in codes:
            family = fam
            break
    if family is None:
        for t in tags:
            if t in FAMILY_TAGS:
                family = FAMILY_TAGS[t]
                break
    if family is None:
        for prefix, fam in CODE_PREFIXES:
            if code.startswith(prefix):
                family = fam
                break
    family = family or "other"

    if family == "esports":
        return family, f"esports-{code}"
    lname = (name or "").lower()
    if code in WOMEN_CODES or "women" in lname or "frauen" in lname or lname.endswith(" w"):
        return family, f"{family}-w"
    if family == "other":
        return family, f"other-{code}"
    return family, family


def is_neutral(family: str) -> bool:
    return family in NEUTRAL_FAMILIES


_VS = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)


def parse_sides(title: str) -> tuple[str, str] | None:
    """'Seoul: A vs B', 'A vs. B (BO3) - Turnier', 'A vs. B - More Markets' -> (A, B)."""
    if not title or not _VS.search(title):
        return None
    t = title
    head, sep, rest = t.partition(": ")
    if sep and _VS.search(rest):
        t = rest
    t = re.sub(r"\s*\(.*$", "", t)
    t = re.sub(r"\s+-\s+.*$", "", t)
    parts = _VS.split(t, maxsplit=1)
    if len(parts) != 2:
        return None
    a, b = parts[0].strip(), parts[1].split(": ")[0].strip()
    return (a, b) if a and b else None


_SCORE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")


def parse_score(score: str | None) -> tuple[float, float] | None:
    """Nur einfache Ergebnisse wie '124-114'. Satz-Ergebnisse (Tennis) werden ignoriert."""
    if not score:
        return None
    m = _SCORE.match(score)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def outcome_de(outcome: str | None, title: str | None = None) -> str:
    """Polymarket-Outcome auf Deutsch, bei Über/Unter mit Linie aus dem Titel ('Over' + 'O/U 225.5' -> 'Über 225,5')."""
    o = {"Yes": "Ja", "No": "Nein", "Over": "Über", "Under": "Unter"}.get(outcome or "", outcome or "")
    if o in ("Über", "Unter"):
        m = re.search(r"O/U\s*([\d.]+)", title or "")
        if m:
            o += " " + m.group(1).replace(".", ",")
    return o


def excluded(family: str | None) -> bool:
    from . import config

    return (family or "") in config.EXCLUDED_FAMILIES


_STOP = {"fc", "sc", "cf", "ac", "as", "sv", "vfb", "vfl", "tsg", "fk", "sk", "ss", "ssc", "afc", "cd", "ud", "rc", "rcd",
         "club", "de", "cp", "bk", "if", "ik", "hc", "bc", "kk", "fsv", "1", "1899", "04", "05", "09", "the", "real", "city",
         "united", "st", "us", "osc", "dsc", "es", "rcd", "ec", "se", "cr", "ogc", "aj", "sl", "fsv", "hsg", "tsv", "sg", "spvgg", "kv", "krc"}
_UMLAUT = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss", "é": "e", "è": "e", "á": "a", "à": "a", "í": "i", "ó": "o",
                         "ú": "u", "ñ": "n", "ç": "c", "ø": "o", "å": "a", "ł": "l", "ś": "s", "ž": "z", "č": "c", "ć": "c", "ș": "s", "ţ": "t", "ş": "s"})


def search_term(name: str, family: str | None = None) -> str:
    """Suchbegriff für Quotenvergleiche: bei Einzelsport der Nachname, sonst das markanteste Wort des Teamnamens
    ('FC Bayern München' -> 'Bayern', '1. FC Köln' -> 'Koln', 'Carlos Alcaraz' -> 'Alcaraz')."""
    n = (name or "").translate(_UMLAUT)
    words = [w for w in re.split(r"[\s./()-]+", n) if w]
    if not words:
        return ""
    if is_neutral(family or ""):
        return words[-1]
    good = [w for w in words if w.lower() not in _STOP and not w.isdigit() and len(w) > 2]
    if len(words) == 2 and len(good) == 2:  # US-Spitznamen: "Trail Blazers", "Maple Leafs"
        return " ".join(words)
    return (good or words)[0]


def odds_link(title_or_name: str, family: str | None = None) -> str | None:
    """Link zur OddsPortal-Suche (in Deutschland erreichbar, zeigt Quoten deutscher Anbieter)."""
    sides = parse_sides(title_or_name or "")
    term = search_term(sides[0] if sides else title_or_name, family)
    return f"https://www.oddsportal.com/search/{term}/" if term else None


# Schlichte deutsche Namen der Sportarten (für die Web-App)
SPORT_NAME = {
    "soccer": "Fußball", "basketball": "Basketball", "tennis": "Tennis", "american-football": "American Football",
    "hockey": "Eishockey", "baseball": "Baseball", "mma": "MMA", "boxing": "Boxen", "darts": "Darts",
    "table-tennis": "Tischtennis", "cricket": "Cricket", "handball": "Handball", "volleyball": "Volleyball",
    "rugby": "Rugby", "aussie-rules": "Aussie Rules", "golf": "Golf", "racing": "Motorsport", "chess": "Schach",
    "pickleball": "Pickleball", "lacrosse": "Lacrosse", "other": "Sonstiges",
}
WALLET_DE = {"Sharp": "Profi", "Neutral": "Neutral", "Frische Wallet": "Neues Konto", "Dumb Money": "Verlierer"}
