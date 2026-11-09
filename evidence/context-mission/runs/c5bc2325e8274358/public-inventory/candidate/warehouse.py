def reserve(stock, quantities):
    """Atomically reserve quantities from stock.

    Returns True and deducts from ``stock`` only if every combined quantity is
    available; otherwise returns False and leaves ``stock`` untouched. Unknown
    SKUs count as unavailable.
    """
    for sku, qty in quantities.items():
        if sku not in stock or stock[sku] < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
