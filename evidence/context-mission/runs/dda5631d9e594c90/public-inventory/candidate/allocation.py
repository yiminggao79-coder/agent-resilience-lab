from lines import combine, identity
from warehouse import reserve


def allocate(stock, orders):
    """Allocate orders against a copy of ``stock`` in arrival order.

    Returns ``{"remaining": stock_after, "results": rows}`` where each row is
    ``{"id": order_id, "accepted": bool}``. Caller inputs are never mutated.

    Order IDs are idempotent within a single call: replaying an ID with the same
    combined SKU quantities reproduces the original outcome without consuming
    stock again. Reusing an ID with different combined quantities raises
    ``ValueError``; this is detected before any allocation work is done.
    """
    # Validate identities first so a conflicting ID leaves caller inputs intact
    # and no partial allocation is returned or applied.
    outcomes = {}
    for order in orders:
        order_id = order['id']
        key = identity(order['items'])
        previous = outcomes.get(order_id)
        if previous is None:
            outcomes[order_id] = key
        elif previous != key:
            raise ValueError('conflicting quantities for order id %r' % (order_id,))

    remaining = dict(stock)
    seen = {}
    results = []
    for order in orders:
        order_id = order['id']
        key = identity(order['items'])
        if order_id in seen:
            accepted = seen[order_id]
        else:
            accepted = reserve(remaining, combine(order['items']))
            seen[order_id] = accepted
        results.append({'id': order_id, 'accepted': accepted})
    return {'remaining': remaining, 'results': results}
