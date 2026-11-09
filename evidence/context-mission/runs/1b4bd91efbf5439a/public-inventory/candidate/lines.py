def quantities(items):
    """Combine repeated SKU lines in one order into summed quantities."""
    combined = {}
    for item in items:
        sku = item['sku']
        combined[sku] = combined.get(sku, 0) + item['qty']
    return combined


def identity(combined):
    """Order identity for replay detection.

    Depends only on the set of SKU -> total qty pairs, so line order and
    splitting one SKU across several lines do not affect it.
    """
    return tuple(sorted(combined.items()))
