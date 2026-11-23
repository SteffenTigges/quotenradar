import datetime as dt

from edgelab import darts
from edgelab.families import league_tier


def ts(y, m, d):
    return int(dt.datetime(y, m, d, 20, tzinfo=dt.timezone.utc).timestamp())


def test_world_championship_sets_by_round():
    t = "PDC World Championship"
    assert darts.sets_to(t, ts(2026, 12, 12)) == 3
    assert darts.sets_to(t, ts(2026, 12, 28)) == 4
    assert darts.sets_to(t, ts(2027, 1, 1)) == 5
    assert darts.sets_to(t, ts(2027, 1, 2)) == 6
    assert darts.sets_to(t, ts(2027, 1, 3)) == 7
    assert darts.sets_to("Players Championship Finals", ts(2026, 11, 27)) is None
    assert darts.sets_to("Women's World Championship", ts(2026, 12, 12)) is None


def test_sets_model_is_consistent():
    p_set = darts.set_prob(98, 92)
    assert 0.5 < p_set < 1
    # Längere Spiele bevorzugen den Stärkeren
    assert darts.match_prob_sets(98, 92, 7) > darts.match_prob_sets(98, 92, 3) > p_set
    groups = darts.markets_sets("A", "B", 98, 92, 3)
    assert [g["group"] for g in groups][:2] == ["Sieger", "Satzergebnis"]
    assert abs(sum(i["p"] for i in groups[1]["items"]) - 1) < 1e-6
    assert len(groups[1]["items"]) == 6


def test_darts_importance():
    assert league_tier("darts", "World Darts Championship")[0] == 1
    assert league_tier("darts", "Grand Slam of Darts")[0] == 2
    assert league_tier("darts", "Players Championship Finals")[0] == 2
    assert league_tier("darts", "Players Championship 33")[0] == 3
    assert league_tier("darts", "MODUS Super Series")[0] == 4
    assert league_tier("darts", "World Youth Championship")[0] == 4
