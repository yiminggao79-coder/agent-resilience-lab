"""Public batch dispatch API."""

from catalog import prepare_request
from inventory import reserve_request


def plan_dispatch(stock, orders):
    remaining = dict(stock)
    results = []
    for order in orders:
        request = prepare_request(order["lines"])
        shortages = reserve_request(remaining, request)
        results.append({
            "order_id": order["order_id"],
            "status": "rejected" if shortages else "reserved",
            "items": [{"sku": sku, "quantity": quantity} for sku, quantity in sorted(request.items())],
            "shortages": shortages,
        })
    return {"remaining": remaining, "orders": results}
