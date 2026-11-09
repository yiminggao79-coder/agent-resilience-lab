from decimal import Decimal, ROUND_HALF_UP

_TWO_PLACES = Decimal("0.01")


def money(value):
    """Quantize a Decimal to two places using ROUND_HALF_UP and format it."""
    return str(Decimal(value).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP))
