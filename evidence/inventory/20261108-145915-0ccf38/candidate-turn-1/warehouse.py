def reserve(stock, quantities):
    """Reserve every quantity atomically.

    Returns True and deducts the quantities from ``stock`` (mutating it in
    place) only when all lines are available; otherwise returns False and
    leaves ``stock`` untouched. Unknown SKUs are unavailable.
    """
    for sku, qty in quantities.items():
        if sku not in stock or stock[sku] < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
