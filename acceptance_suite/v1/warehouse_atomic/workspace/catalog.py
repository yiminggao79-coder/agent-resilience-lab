"""Translate order lines into inventory requests."""


def prepare_request(lines):
    request = {}
    for line in lines:
        sku = line["sku"].strip()
        request[sku] = request.get(sku, 0) + line["quantity"]
    return request
