def quantities(items):
    """Combine repeated SKU lines within a single order."""
    combined = {}
    for item in items:
        sku = item['sku']
        combined[sku] = combined.get(sku, 0) + item['qty']
    return combined
