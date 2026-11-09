def quantities(items):
    """Combine repeated SKU lines in one order, preserving case-sensitive SKUs."""
    combined = {}
    for item in items:
        sku = item['sku']
        combined[sku] = combined.get(sku, 0) + item['qty']
    return combined
