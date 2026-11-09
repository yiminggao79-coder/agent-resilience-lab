def reserve(stock, quantities):
    """Reserve quantities atomically, returning False without changes on failure."""
    for sku, qty in quantities.items():
        if sku not in stock or stock[sku] < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
