from lines import quantities
from warehouse import reserve


def allocate(stock, orders):
    """Allocate stock to orders in arrival order.

    Within one call an order ``id`` is idempotent: a replay with the same
    combined SKU quantities reproduces the original verdict without touching
    stock again (line order and line splitting are irrelevant to identity).
    Reusing an ``id`` with different combined quantities raises ValueError.
    Caller inputs are never mutated, including on that error.
    """
    remaining = dict(stock)
    results = []
    seen = {}
    for order in orders:
        order_id = order['id']
        wanted = quantities(order['items'])
        if order_id in seen:
            previous_quantities, previous_accepted = seen[order_id]
            if previous_quantities != wanted:
                raise ValueError(
                    'order id %r reused with different quantities' % (order_id,))
            results.append({'id': order_id, 'accepted': previous_accepted})
            continue
        updated = reserve(remaining, wanted)
        accepted = updated is not None
        if accepted:
            remaining = updated
        seen[order_id] = (wanted, accepted)
        results.append({'id': order_id, 'accepted': accepted})
    return {'remaining': remaining, 'results': results}
