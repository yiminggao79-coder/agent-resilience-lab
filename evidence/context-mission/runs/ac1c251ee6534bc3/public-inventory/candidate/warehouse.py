def reserve(stock, quantities):
    """Atomically reserve quantities from stock.

    Returns True and decreases stock only if every requested SKU has enough
    available quantity. Unknown SKUs count as unavailable. Otherwise returns
    False and leaves stock untouched.
    """
    for sku, qty in quantities.items():
        if sku not in stock or stock[sku] < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
