def reserve(stock, quantities):
    """Atomically reserve quantities from stock.

    Returns True and deducts all requested quantities when every requested
    SKU is available in sufficient quantity; otherwise returns False and
    leaves stock untouched. `stock` is modified only on success.
    """
    for sku, qty in quantities.items():
        if stock.get(sku, 0) < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
