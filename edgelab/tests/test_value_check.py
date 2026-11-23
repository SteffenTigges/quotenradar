from edgelab.games import _book_row, books_rows, disagrees_with_market


def test_small_disagreement_is_fine():
    assert not disagrees_with_market(0.43, 0.34)   # 9 Punkte, Faktor 1,26
    assert not disagrees_with_market(0.52, 0.48)


def test_big_disagreement_is_unsure():
    assert disagrees_with_market(0.23, 0.09)        # Außenseiter 2,5-mal so wahrscheinlich
    assert disagrees_with_market(0.75, 0.57)        # 18 Punkte
    assert disagrees_with_market(0.35, 0.26)        # Faktor 1,35


def _game(model, h2h):
    return {"a": "Real", "b": "PSV", "model": model, "groups": [], "books": {"h2h": h2h}}


def test_rows_use_consensus_of_all_books():
    g = _game({"a": 0.55, "draw": 0.22, "b": 0.23},
              {"Tipico": {"a": 1.30, "draw": 6.0, "b": 9.0}, "Winamax": {"a": 1.28, "draw": 6.5, "b": 10.5}})
    rows = {r["tipp"]: r for r in books_rows(g)}
    psv = rows["Sieg PSV"]
    assert psv["best"] == "Winamax" and psv["value"] > 1
    assert psv["unsure"]                            # riesiger „Value“ wird als unsicher markiert


def test_plausible_value_stays():
    g = _game({"a": 0.47, "draw": 0.26, "b": 0.27},
              {"Tipico": {"a": 2.25, "draw": 3.5, "b": 3.2}, "Winamax": {"a": 2.30, "draw": 3.4, "b": 3.1}})
    rows = {r["tipp"]: r for r in books_rows(g)}
    assert rows["Sieg Real"]["value"] > 0.05 and not rows["Sieg Real"]["unsure"]


def test_single_offer_without_other_side():
    assert _book_row("Über 2,5", 0.60, {"Tipico": 1.70})["unsure"] is False
    assert _book_row("Über 2,5", 0.80, {"Tipico": 2.40})["unsure"] is True
