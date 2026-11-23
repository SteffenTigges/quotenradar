import pandas as pd

from edgelab.bookmakers import best_value, find_event, for_game, name_similarity


def test_name_similarity():
    assert name_similarity("FC Bayern München", "Bayern Munich") >= 0.9
    assert name_similarity("1. FC Köln", "FC Koln") >= 0.9
    assert name_similarity("Knicks", "New York Knicks") == 1.0
    assert name_similarity("Borussia Dortmund", "Bayern Munich") < 0.8


def _odds():
    rows = []
    for book, (h, d, a) in {"Tipico": (1.07, 15.0, 21.0), "Winamax": (1.06, 13.0, 18.0)}.items():
        for out, p in (("Bayern Munich", h), ("Draw", d), ("FC Koln", a)):
            rows.append(dict(event_id="e1", sport="soccer_germany_bundesliga", commence=1000, home="Bayern Munich", away="FC Koln",
                             book=book, market="h2h", outcome=out, point=-999.0, price=p, updated=0))
        rows.append(dict(event_id="e1", sport="x", commence=1000, home="Bayern Munich", away="FC Koln", book=book, market="totals",
                         outcome="Over", point=2.5, price=1.3, updated=0))
    return pd.DataFrame(rows)


def test_find_and_map_game():
    o = _odds()
    assert find_event("FC Bayern München", "1. FC Köln", 1000 + 3600, o) == "e1"
    assert find_event("Borussia Dortmund", "1. FC Köln", 1000, o) is None
    g = for_game("FC Bayern München", "1. FC Köln", 1000, o)
    assert g["h2h"]["Tipico"] == {"a": 1.07, "draw": 15.0, "b": 21.0}
    # Reihenfolge vertauscht -> a/b vertauscht
    g2 = for_game("1. FC Köln", "FC Bayern München", 1000, o)
    assert g2["h2h"]["Winamax"]["a"] == 18.0
    assert g["totals"]["2.5"]["Tipico"]["over"] == 1.3


def test_best_value():
    book, v = best_value(0.5, {"Tipico": 1.9, "Winamax": 2.1})
    assert book == "Winamax" and abs(v - 0.05) < 1e-9


def test_sport_must_match():
    o = _odds()  # Fußball (soccer_...)
    assert find_event("FC Bayern München", "1. FC Köln", 1000, o, "soccer") == "e1"
    assert find_event("EHC Red Bull München", "Kölner Haie", 1000, o, "hockey") is None
