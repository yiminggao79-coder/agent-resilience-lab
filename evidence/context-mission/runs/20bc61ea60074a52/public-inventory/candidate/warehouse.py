def reserve(stock, quantities):
    for sku, qty in quantities.items():
        available = stock.get(sku)
        if available is None or available < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
