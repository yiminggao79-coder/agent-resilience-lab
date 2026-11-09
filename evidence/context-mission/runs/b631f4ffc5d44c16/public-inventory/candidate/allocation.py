from lines import quantities
from warehouse import reserve


def allocate(stock, orders):
    remaining = dict(stock)
    results = []
    seen = {}
    for order in orders:
        order_id = order['id']
        combined = quantities(order['items'])
        identity = frozenset(combined.items())
        if order_id in seen:
            if seen[order_id][0] != identity:
                raise ValueError(
                    'order id %r reused with different quantities' % (order_id,))
            accepted = seen[order_id][1]
        else:
            accepted = reserve(remaining, combined)
            seen[order_id] = (identity, accepted)
        results.append({'id': order_id, 'accepted': accepted})
    return {'remaining': remaining, 'results': results}
