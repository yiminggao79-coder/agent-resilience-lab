from lines import quantities
from warehouse import reserve


def _identity(quantities):
    """Canonical, order-independent identity of a combined quantity mapping."""
    return tuple(sorted(quantities.items()))


class Inventory(object):
    """Stateful allocator whose stock and ID replay memory persist per instance.

    Constructor stock is copied, so the caller's mapping is never mutated.
    ``allocate`` shares the result schema and allocation rules of the module
    ``allocate`` function, but remaining stock and remembered ids carry over
    between calls on the same instance: replays reproduce the original
    outcome without consuming stock again, and previously rejected ids stay
    rejected. Each batch is atomic -- if an id conflict raises ``ValueError``,
    neither stock nor replay memory from that batch is kept, while earlier
    committed batches remain in effect.
    """

    def __init__(self, stock):
        self._stock = dict(stock)
        self._seen = {}

    def snapshot(self):
        """Return a detached copy of the current stock mapping."""
        return dict(self._stock)

    def allocate(self, orders):
        """Allocate one batch of orders against this inventory's stock.

        Returns ``{"remaining": stock_after, "results": rows}`` where each row
        is ``{"id": order_id, "accepted": bool}`` and ``remaining`` is a
        detached copy of the post-batch stock.
        """
        working = dict(self._stock)
        seen = dict(self._seen)
        results = []
        for order in orders:
            order_id = order['id']
            combined = quantities(order['items'])
            identity = _identity(combined)
            if order_id in seen:
                seen_identity, accepted = seen[order_id]
                if seen_identity != identity:
                    raise ValueError(
                        'order id %r reused with different quantities'
                        % (order_id,)
                    )
            else:
                accepted = reserve(working, combined)
                seen[order_id] = (identity, accepted)
            results.append({'id': order_id, 'accepted': accepted})
        self._stock = working
        self._seen = seen
        return {'remaining': dict(self._stock), 'results': results}


def allocate(stock, orders):
    """Allocate stock to orders in arrival order without mutating inputs.

    Returns ``{"remaining": stock_after, "results": rows}`` where each row is
    ``{\"id\": order_id, \"accepted\": bool}``.

    Idempotency: within one call, an order id is processed at most once. A
    replay (same id, same combined SKU quantities) reproduces the original
    accepted/rejected outcome without touching stock again, even if the
    original outcome was a rejection. Reusing an id with different combined
    quantities raises ValueError; inputs are never modified.

    This is a stateless wrapper: each call uses a fresh ``Inventory``, so no
    stock or id memory survives between calls.
    """
    return Inventory(stock).allocate(orders)
