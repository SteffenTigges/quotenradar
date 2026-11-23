from edgelab.families import league_tier


def test_big_leagues_come_first():
    assert league_tier("basketball", "NBA")[0] == 1
    assert league_tier("soccer", "Bundesliga")[0] == 1
    assert league_tier("basketball", "WNBA")[0] == 3
    assert league_tier("basketball", "NBA") < league_tier("basketball", "WNBA")


def test_same_name_depends_on_sport():
    assert league_tier("soccer", "Serie A")[0] == 1
    assert league_tier("soccer", "Brasileirão Série A")[0] == 3
    assert league_tier("hockey", "National League")[0] == 3
    assert league_tier("soccer", "National League")[0] == 4


def test_unknown_league_is_last():
    assert league_tier("soccer", "Bolivian Primera División") == league_tier("tennis", "ITF")
    assert league_tier(None, None)[0] == 4
