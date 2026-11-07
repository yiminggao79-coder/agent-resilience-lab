def reserve(stock, quantities):
    for sku, qty in quantities.items():
        if stock.get(sku, 0) < qty:
            return False
        stock[sku] -= qty
    return True
