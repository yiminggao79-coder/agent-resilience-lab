def reserve(stock, quantities):
    """Atomically deduct `quantities` from the `stock` dict (mutated internally).

    Returns True if every SKU had enough stock, in which case the deductions are
    applied; returns False and leaves `stock` untouched otherwise.
    """
    for sku, qty in quantities.items():
        if stock.get(sku, 0) < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] = stock.get(sku, 0) - qty
    return True
