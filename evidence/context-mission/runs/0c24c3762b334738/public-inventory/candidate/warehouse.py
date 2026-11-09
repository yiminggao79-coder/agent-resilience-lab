def can_reserve(stock, quantities):
    """Return True only when every combined quantity is fully available."""
    for sku, qty in quantities.items():
        if sku not in stock or stock[sku] < qty:
            return False
    return True


def reserve(stock, quantities):
    """Deduct quantities from stock atomically.

    Returns True when the whole request fit (and was deducted), False
    otherwise (leaving stock untouched). Unknown SKUs are unavailable.
    The caller owns ``stock``; callers wanting immutability should pass a copy.
    """
    if not can_reserve(stock, quantities):
        return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
