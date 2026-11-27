from unittest import mock

from edgelab import export


def _game(darts):
    return {"code": "dar", "a": "A", "b": "B", "start": 1795000000, "family": "darts", "league": "Players Championship",
            "model": {"a": 0.6, "b": 0.4}, "groups": [], "props": [], "liquidity": 1.0, "darts": darts}


def test_export_darts_without_player_stats():
    with mock.patch.object(export.games, "build", return_value=[_game({"format": "Best of 11 Legs"})]):
        out = export.export_games(48)
    assert out[0]["darts"] == {"format": "Best of 11 Legs"}


def test_export_darts_with_player_stats():
    st = {"avg": 95.0, "first9": 101.0, "s180": 0.3, "checkout": 40.0, "legs_won": 55.0, "high_co": 160, "slug": "x"}
    with mock.patch.object(export.games, "build", return_value=[_game({"a": st, "b": st, "format": "Best of 11 Legs"})]):
        out = export.export_games(48)
    assert out[0]["darts"]["a"]["avg"] == 95.0 and "slug" not in out[0]["darts"]["a"]
