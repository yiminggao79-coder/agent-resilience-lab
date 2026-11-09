from lines import quantities
from warehouse import reserve


def allocate(stock, orders):
    """Allocate stock to orders in arrival order.

    Returns ``{"remaining": stock_after, "results": rows}`` where each row is
    exactly ``{"id": order_id, "accepted": bool}``. Neither ``stock`` nor
    ``orders`` is mutated; ``remaining`` is a detached copy that preserves all
    original keys (including zero counts) plus any keys created by allocation.

    Idempotency: within one call an order ID is processed at most once. A
    repeated ID with the same combined SKU quantities replays the original
    result without consuming stock again; a repeated ID with different combined
    quantities raises ``ValueError``. Caller inputs stay unchanged on that error.
    """
    remaining = dict(stock)
    seen = {}
    results = []
    for order in orders:
        order_id = order['id']
        combined = quantities(order['items'])
        if order_id in seen:
            first_combined, accepted = seen[order_id]
            if first_combined != combined:
                raise ValueError(
                    'order id %r reused with different quantities' % (order_id,))
        else:
            accepted = reserve(remaining, combined)
            seen[order_id] = (combined, accepted)
        results.append({'id': order_id, 'accepted': accepted})
    return {'remaining': remaining, 'results': results}
