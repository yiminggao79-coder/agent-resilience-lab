from lines import quantities
from warehouse import reserve


def allocate(stock, orders):
    """Allocate orders against stock in arrival order, idempotently by ID.

    Returns ``{"remaining": stock_after, "results": rows}`` where each row is
    exactly ``{"id": order_id, "accepted": bool}``. Inputs are never mutated,
    including when ``ValueError`` is raised.

    Within one call, an ID's identity is its combined SKU quantities: the same
    ID with the same combined quantities (regardless of line order or how the
    SKU was split across lines) replays the original verdict without consuming
    stock again; a rejected ID stays rejected. The same ID with different
    combined quantities raises ``ValueError``.
    """
    remaining = dict(stock)
    results = []
    decided = {}
    for order in orders:
        order_id = order['id']
        combined = quantities(order['items'])
        identity = frozenset(combined.items())
        if order_id in decided:
            seen_identity, accepted = decided[order_id]
            if identity != seen_identity:
                raise ValueError(
                    'order id %r reused with different combined quantities'
                    % (order_id,))
        else:
            accepted = reserve(remaining, combined)
            decided[order_id] = (identity, accepted)
        results.append({'id': order_id, 'accepted': accepted})
    return {'remaining': remaining, 'results': results}
