def can_reserve(stock, quantities):
    """Return True only when every combined quantity is fully available.

    Unknown SKUs count as unavailable. Does not modify ``stock``.
    """
    for sku, qty in quantities.items():
        if stock.get(sku, 0) < qty:
            return False
    return True


def commit(stock, quantities):
    """Deduct an already-validated set of quantities from a private stock dict."""
    for sku, qty in quantities.items():
        stock[sku] -= qty


def reserve(stock, quantities):
    """Atomically reserve quantities against ``stock``; on failure nothing changes."""
    if not can_reserve(stock, quantities):
        return False
    commit(stock, quantities)
    return True
