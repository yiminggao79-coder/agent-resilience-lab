"""Translate order lines into inventory requests."""


def normalize_sku(sku):
    """Return the canonical form of a requested SKU."""
    return sku.strip().upper()


def prepare_request(lines):
    """Combine duplicate lines into a single normalized SKU -> quantity mapping."""
    request = {}
    for line in lines:
        sku = normalize_sku(line["sku"])
        request[sku] = request.get(sku, 0) + line["quantity"]
    return request


def request_items(request):
    """Represent a prepared request as a SKU-sorted list of line dicts."""
    return [{"sku": sku, "quantity": quantity} for sku, quantity in sorted(request.items())]
