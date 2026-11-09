def reserve(stock, quantities):
    """Atomically reserve combined quantities from stock.

    The order is accepted only when every combined quantity is available;
    unknown SKUs are treated as unavailable. Rejection consumes no stock.
    Validation happens before any mutation, so a failed reservation leaves
    ``stock`` untouched.
    """
    for sku, qty in quantities.items():
        if qty > stock.get(sku, 0):
            return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
