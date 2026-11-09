"""Apply a prepared request to a working stock dictionary."""


def find_shortages(available, request):
    """Return every short SKU, SKU-sorted, without touching available."""
    shortages = []
    for sku, quantity in sorted(request.items()):
        current = available.get(sku, 0)
        if quantity > current:
            shortages.append({"sku": sku, "requested": quantity, "available": current})
    return shortages


def reserve_request(available, request):
    """Reserve the whole request when possible.

    Returns the (possibly empty) list of shortages. The request is reserved only
    if nothing is short; otherwise ``available`` is left unchanged. Unknown SKUs
    are never added to ``available``.
    """
    shortages = find_shortages(available, request)
    if shortages:
        return shortages
    for sku, quantity in request.items():
        available[sku] = available[sku] - quantity
    return shortages
