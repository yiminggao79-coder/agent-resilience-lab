def combine(items):
    """Combine repeated SKU lines within one order into a single total per SKU."""
    combined = {}
    for item in items:
        sku = item['sku']
        combined[sku] = combined.get(sku, 0) + item['qty']
    return combined


def quantities(items):
    """Combine repeated SKU lines within one order into a single total per SKU."""
    return combine(items)


def identity(items):
    """Canonical order identity: combined quantities, independent of line order/splitting."""
    return tuple(sorted(combine(items).items()))
