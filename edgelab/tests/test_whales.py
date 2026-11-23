from edgelab.whales import classify_wallet, trade_pnl

NOW = 1_800_000_000
DAY = 86400


def test_sharp():
    assert classify_wallet(NOW - 400 * DAY, 5000, 150_000, 3_000_000, NOW) == "Sharp"


def test_low_roi_big_volume_is_not_sharp():
    assert classify_wallet(NOW - 400 * DAY, 5000, 25_000, 30_000_000, NOW) == "Neutral"


def test_dumb_money():
    assert classify_wallet(NOW - 400 * DAY, 800, -40_000, 900_000, NOW) == "Dumb Money"


def test_fresh_wallet():
    assert classify_wallet(NOW - 3 * DAY, 2, 0, 5_000, NOW) == "Frische Wallet"
    assert classify_wallet(None, 4, None, None, NOW) == "Frische Wallet"


def test_trade_pnl():
    # 1000 Anteile zu 0.40 gekauft, Outcome gewinnt -> +600
    assert trade_pnl("BUY", 1000, 0.40, 1.0) == 600
    assert trade_pnl("BUY", 1000, 0.40, 0.0) == -400
    # Verkauf zu 0.70, Outcome verliert -> +700
    assert trade_pnl("SELL", 1000, 0.70, 0.0) == 700
    assert trade_pnl("BUY", 1000, 0.40, None) is None
