from decimal import Decimal

from money import money
from parsing import parse_lines


def total(parsed):
    """Sum ``unit_price * qty`` over parsed lines and round once, half up."""
    return money(sum((price * qty for price, qty in parsed), Decimal(0)))


def total_for(lines):
    """Validate raw invoice lines, then total them."""
    return total(parse_lines(lines))
