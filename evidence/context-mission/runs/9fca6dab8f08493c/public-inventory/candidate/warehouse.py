def available(stock, quantities):
    """Return True only when every combined quantity can be satisfied."""
    for sku, qty in quantities.items():
        if stock.get(sku, 0) < qty:
            return False
    return True


def reserve(stock, quantities):
    """Atomically deduct an order from ``stock``.

    Returns True when accepted (stock is updated), False when rejected
    (stock is left untouched). Unknown SKUs count as unavailable.
    """
    if not available(stock, quantities):
        return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
