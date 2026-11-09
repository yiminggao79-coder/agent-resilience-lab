def reserve(stock, quantities):
    """Atomically reserve quantities from stock, or leave stock untouched.

    Returns True if all quantities (including unknown SKUs) are available and
    were deducted; returns False without changing stock otherwise.
    """
    for sku, qty in quantities.items():
        if stock.get(sku, 0) < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
