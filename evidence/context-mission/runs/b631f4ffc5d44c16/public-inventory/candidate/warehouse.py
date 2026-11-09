def reserve(stock, quantities):
    # Validate every line before mutating so rejection consumes no stock.
    for sku, qty in quantities.items():
        if sku not in stock or stock[sku] < qty:
            return False
    for sku, qty in quantities.items():
        stock[sku] -= qty
    return True
