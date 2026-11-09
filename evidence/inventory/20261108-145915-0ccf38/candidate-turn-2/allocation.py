from lines import quantities
from warehouse import reserve


def _identity(combined):
    """Order-insensitive identity for a set of combined SKU quantities."""
    return frozenset(combined.items())


def allocate(stock, orders):
    remaining = dict(stock)
    results = []
    seen = {}
    for order in orders:
        order_id = order['id']
        merged = quantities(order['items'])
        identity = _identity(merged)
        if order_id in seen:
            prior_identity, prior_accepted = seen[order_id]
            if identity != prior_identity:
                raise ValueError(
                    'order id %r reused with different quantities' % (order_id,))
            accepted = prior_accepted
        else:
            accepted = reserve(remaining, merged)
            seen[order_id] = (identity, accepted)
        results.append({'id': order_id, 'accepted': accepted})
    return {'remaining': remaining, 'results': results}
