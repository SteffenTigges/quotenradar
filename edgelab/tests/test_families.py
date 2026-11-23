from edgelab.families import classify, parse_sides, parse_score, is_neutral


def test_classify_by_tag():
    assert classify("bun", "Bundesliga", "1,100639,100350") == ("soccer", "soccer")
    assert classify("nba", "NBA", "1,745,100639,28") == ("basketball", "basketball")


def test_classify_by_code_fallback():
    assert classify("nfl", "NFL", "1,450")[0] == "american-football"
    assert classify("atp", "ATP", "1,101232")[0] == "tennis"
    assert classify("ufc", "UFC", "1,279")[0] == "mma"
    assert classify("bkbbl", "BBL", "1")[0] == "basketball"


def test_esports_pool_per_game_and_women_split():
    assert classify("cs2", "Counter Strike", "1,64") == ("esports", "esports-cs2")
    assert classify("lol", "League of Legends", "1,64") == ("esports", "esports-lol")
    assert classify("wsl", "Women's Super League", "1,100350") == ("soccer", "soccer-w")
    assert classify("wnba", "WNBA", "1,28") == ("basketball", "basketball-w")


def test_parse_sides():
    assert parse_sides("Lakers vs. Hawks") == ("Lakers", "Hawks")
    assert parse_sides("Seoul: Terence Atmane vs Coleman Wong") == ("Terence Atmane", "Coleman Wong")
    assert parse_sides("Counter-Strike: BOJONG vs eSuba (BO1) - ESEA Advanced Europe Regular Season") == (
        "BOJONG",
        "eSuba",
    )
    assert parse_sides("UFC 334: Alatengheili vs. Juan Diaz (Bantamweight, Early Prelims)") == (
        "Alatengheili",
        "Juan Diaz",
    )
    assert parse_sides("TSG 1899 Hoffenheim vs. FC Schalke 04 - More Markets") == (
        "TSG 1899 Hoffenheim",
        "FC Schalke 04",
    )
    assert parse_sides("PGA Tour: World Wide Technology Championship Winner") is None


def test_parse_score():
    assert parse_score("124-114") == (124.0, 114.0)
    assert parse_score("0-0") == (0.0, 0.0)
    assert parse_score("7-6(7-3), 3-6, 7-6(11-9)") is None
    assert parse_score(None) is None


def test_neutral():
    assert is_neutral("tennis") and is_neutral("esports") and not is_neutral("soccer")


def test_search_term():
    from edgelab.families import odds_link, search_term

    assert search_term("FC Bayern München", "soccer") == "Bayern"
    assert search_term("1. FC Köln", "soccer") == "Koln"
    assert search_term("Carlos Alcaraz", "tennis") == "Alcaraz"
    assert search_term("Knicks", "basketball") == "Knicks"
    assert search_term("Real Madrid CF", "soccer") == "Madrid"
    assert odds_link("Lakers vs. Hawks", "basketball") == "https://www.oddsportal.com/search/Lakers/"


def test_esports_excluded_by_default():
    from edgelab.families import excluded

    assert excluded("esports") and not excluded("soccer")


def test_search_term_two_word_nicknames():
    from edgelab.families import search_term

    assert search_term("Trail Blazers", "basketball") == "Trail Blazers"
    assert search_term("DSC Arminia Bielefeld", "soccer") == "Arminia"
    assert search_term("Athletic Club", "soccer") == "Athletic"


def test_parse_sides_with_market_suffix():
    assert parse_sides("Clippers vs. Spurs: O/U 225.5") == ("Clippers", "Spurs")


def test_outcome_de():
    from edgelab.families import outcome_de

    assert outcome_de("Over", "Clippers vs. Spurs: O/U 225.5") == "Über 225,5"
    assert outcome_de("Yes", "Will X win?") == "Ja"
    assert outcome_de("Knicks", "Knicks vs. Timberwolves") == "Knicks"
