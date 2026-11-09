def reserve(stock, quantities):
    """Atomically reserve quantities from stock; return the updated stock or None.

    Returns None when any requested SKU (including an unknown one) cannot be
    fully satisfied. No partial reservation is performed and ``stock`` is not
    mutated.
    """
    for sku, qty in quantities.items():
        if stock.get(sku, 0) < qty:
            return None
    updated = dict(stock)
    for sku, qty in quantities.items():
        updated[sku] = updated[sku] - qty
    return updated
