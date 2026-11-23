from edgelab.fx import euro, parse_ecb, prozent, quote, zahl

XML = "<Cube time='2026-11-18'><Cube currency='USD' rate='1.0968'/><Cube currency='JPY' rate='174.85'/></Cube>"


def test_parse_ecb():
    assert parse_ecb(XML) == 1.0968
    assert parse_ecb("<x/>") is None


def test_german_formats():
    assert zahl(1234567.891, 2) == "1.234.567,89"
    assert euro(17967) == "17.967 €"
    assert euro(-1234.5, 2) == "-1.234,50 €" or euro(-1234.5, 2) == "−1.234,50 €" or euro(-1234.5, 2).startswith("-1.234,50")
    assert prozent(0.526) == "52,6 %"
    assert prozent(0.05, vorzeichen=True) == "+5,0 %"
    assert prozent(-0.123, vorzeichen=True) == "−12,3 %"
    assert quote(1.9) == "1,90"
