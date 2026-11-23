import math
from pathlib import Path

from edgelab.darts import first_to, leg_prob, markets, match_prob, parse_profile, scores, slug


def test_slug():
    assert slug("Michael van Gerwen") == "michael-van-gerwen"
    assert slug("Karel Sedláček") == "karel-sedlacek"
    assert slug("William O'Connor") == "william-oconnor"


def test_parse_profile_fixture():
    html = (Path(__file__).parent / "fixtures" / "orakel_price.html").read_text(encoding="utf-8")
    s = parse_profile(html)
    assert s["avg"] == 98.55 and s["s180"] == 485 and s["checkout"] == 43.17 and s["legs_won"] == 56.74


def test_scores_sum_and_symmetry():
    d = scores(0.5, 16)
    assert math.isclose(sum(d.values()), 1.0, abs_tol=1e-9)
    assert math.isclose(sum(v for (a, b), v in d.items() if a > b), 0.5, abs_tol=1e-9)


def test_stronger_player_and_longer_format():
    assert leg_prob(98, 92) > 0.5
    # längeres Format -> Favorit gewinnt öfter
    assert match_prob(98, 92, 16) > match_prob(98, 92, 6) > 0.5


def test_format_and_markets():
    assert first_to("Grand Slam of Darts: Gerwyn Price vs Niko Springer") == 16
    assert first_to("MODUS Super Series: A vs B") is None
    groups = markets("Price", "Springer", 98.55, 92.0, 16)
    names = [g["group"] for g in groups]
    assert names[0] == "Sieger" and "Handicap Legs" in names
    win = groups[0]["items"][0]["p"]
    assert 0.75 < win < 0.95
