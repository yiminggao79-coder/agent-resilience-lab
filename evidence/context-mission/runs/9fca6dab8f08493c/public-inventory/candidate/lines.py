def quantities(items):
    """Combine repeated SKU lines in one order into a single quantity."""
    combined = {}
    for item in items:
        sku = item['sku']
        combined[sku] = combined.get(sku, 0) + item['qty']
    return combined


def identity(combined):
    """Canonical, order-independent identity of combined SKU quantities.

    Two orders share an identity exactly when their combined quantities match,
    regardless of line order, line splitting, or dict insertion order.
    """
    return tuple(sorted(combined.items()))
