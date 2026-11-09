def reserve(stock, quantities):
    for sku, qty in quantities.items():
        if stock.get(sku, 0) < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] = stock.get(sku, 0) - qty
    return True
