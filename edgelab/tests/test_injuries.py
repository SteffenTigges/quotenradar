from edgelab.injuries import basketball_impact, match_team, status_weight


def test_match_team_nickname():
    teams = ["Los Angeles Lakers", "Minnesota Timberwolves", "Philadelphia 76ers", "Portland Trail Blazers"]
    assert match_team("Lakers", teams) == "Los Angeles Lakers"
    assert match_team("Trail Blazers", teams) == "Portland Trail Blazers"
    assert match_team("76ers", teams) == "Philadelphia 76ers"
    assert match_team("Celtics", teams) is None


def test_impact_star_vs_bench():
    star = basketball_impact(30, 6, 6, 1.5, 0.5, 3, 35)
    bench = basketball_impact(5, 2, 1, 0.3, 0.2, 0.8, 12)
    assert 4 < star <= 7
    assert bench < 1


def test_status_weight():
    assert status_weight("Out") == 1.0
    assert status_weight("Active") == 0.0
    assert status_weight("Questionable") < status_weight("Doubtful")
