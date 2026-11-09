from lines import identity, quantities
from warehouse import reserve


def allocate(stock, orders):
    remaining = dict(stock)
    results = []
    seen = {}
    decisions = {}

    # Pre-validate every replay so a mismatched ID raises ValueError before
    # any stock is touched, keeping caller inputs unchanged on error.
    for order in orders:
        key = identity(quantities(order['items']))
        previous = seen.get(order['id'])
        if previous is not None and previous[0] != key:
            raise ValueError('order id reused with different quantities: %r'
                             % (order['id'],))
        seen.setdefault(order['id'], (key, None))

    for order in orders:
        order_id = order['id']
        key = identity(quantities(order['items']))
        if order_id in decisions:
            # Replay: return the original decision, consume nothing.
            accepted = decisions[order_id]
        else:
            accepted = reserve(remaining, quantities(order['items']))
            decisions[order_id] = accepted
        results.append({'id': order_id, 'accepted': accepted})

    return {'remaining': remaining, 'results': results}
