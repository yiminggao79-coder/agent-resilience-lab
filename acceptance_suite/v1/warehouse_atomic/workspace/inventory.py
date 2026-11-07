"""Apply a prepared request to a working stock dictionary."""


def reserve_request(available, request):
    shortages = []
    for sku, quantity in sorted(request.items()):
        current = available.get(sku, 0)
        if quantity > current:
            shortages.append({"sku": sku, "requested": quantity, "available": current})
        else:
            available[sku] = current - quantity
    return shortages
