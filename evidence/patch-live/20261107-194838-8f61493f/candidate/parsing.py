from decimal import Decimal, InvalidOperation


class InvalidLine(ValueError):
    """Raised when an invoice line is malformed."""


def _parse_price(raw):
    if not isinstance(raw, str):
        raise InvalidLine("unit_price must be a string")
    try:
        value = Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        raise InvalidLine("unit_price is not a decimal") from None
    if not value.is_finite() or value < 0:
        raise InvalidLine("unit_price must be a nonnegative finite decimal")
    return value


def _parse_qty(raw):
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise InvalidLine("qty must be a nonnegative integer")
    if raw < 0:
        raise InvalidLine("qty must be a nonnegative integer")
    return raw


def parse_line(line):
    """Validate one invoice line and return ``(unit_price, qty)``."""
    if not isinstance(line, dict):
        raise InvalidLine("line must be a dict")
    if "unit_price" not in line or "qty" not in line:
        raise InvalidLine("line is missing required fields")
    return _parse_price(line["unit_price"]), _parse_qty(line["qty"])


def parse_lines(lines):
    """Validate a sequence of invoice lines and return parsed ``(price, qty)`` pairs."""
    if not isinstance(lines, list):
        raise InvalidLine("lines must be a list")
    return [parse_line(line) for line in lines]
