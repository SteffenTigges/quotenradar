"""Wettmathematik: Gebühren, Erwartungswert (EV) und Kelly-Einsatz."""
from __future__ import annotations


def fee_per_share(price: float, rate: float) -> float:
    """Polymarket-Taker-Gebühr pro Anteil: rate × p × (1 − p)."""
    return rate * price * (1.0 - price)


def ev(prob: float, price: float, fee_rate: float) -> float:
    """Erwarteter Gewinn pro eingesetztem Dollar beim Kauf eines Anteils zum Preis `price`."""
    cost = price + fee_per_share(price, fee_rate)
    if cost <= 0:
        return 0.0
    return (prob - cost) / cost


def kelly(prob: float, price: float, fee_rate: float) -> float:
    """Kelly-Anteil der Bankroll (voller Kelly). 0, wenn kein Vorteil."""
    cost = price + fee_per_share(price, fee_rate)
    if cost >= 1 or cost <= 0:
        return 0.0
    return max(0.0, (prob - cost) / (1 - cost))


def decimal_ev(prob: float, odds: float) -> float:
    """EV pro Einsatz-Einheit bei Dezimalquote (Buchmacher)."""
    return prob * odds - 1.0


def decimal_kelly(prob: float, odds: float) -> float:
    b = odds - 1.0
    if b <= 0:
        return 0.0
    return max(0.0, (prob * b - (1 - prob)) / b)


def price_to_odds(price: float | None) -> float | None:
    return None if not price else 1.0 / price
