"""Inventory allocation engine plus the one-shot ``allocate`` convenience API."""
from lines import identity, quantities
from warehouse import reserve


class Inventory:
    """Persistent inventory whose stock and ID replay memory survive batches.

    Allocation rules match :func:`allocate`: arrival order, per-order SKU
    combining, all-or-nothing atomic reservation, unknown SKUs unavailable,
    rejection consuming nothing, and case-sensitive SKU names. On top of that,
    the stock and the record of previously seen order IDs persist across
    :meth:`allocate` calls, so an equivalent replay in a later batch still
    consumes nothing and a previously rejected ID stays rejected.

    Each batch is atomic with respect to ID conflicts: if a conflict raises
    ``ValueError``, no stock change and no newly remembered ID from that batch
    is kept, while earlier successful batches remain committed.
    """

    def __init__(self, stock):
        # Take a private copy so later caller mutation cannot leak in.
        self._stock = dict(stock)
        self._seen = {}          # order id -> (identity, accepted)

    def snapshot(self):
        """Return a detached copy of current stock."""
        return dict(self._stock)

    def allocate(self, orders):
        """Allocate one batch of orders against the persistent stock.

        Returns ``{"remaining": stock_after, "results": rows}`` where each row
        is exactly ``{"id": order_id, "accepted": bool}``. Caller inputs are
        never mutated and all original stock keys are preserved. The returned
        ``remaining`` is detached from internal state.
        """
        # Stage the whole batch on private copies so a ValueError leaves both
        # the committed stock and the committed ID memory untouched.
        remaining = dict(self._stock)
        seen = dict(self._seen)
        results = []
        try:
            for order in orders:
                order_id = order['id']
                combined = quantities(order['items'])
                key = identity(combined)
                if order_id in seen:
                    recorded_key, accepted = seen[order_id]
                    if recorded_key != key:
                        raise ValueError(
                            'order id %r reused with different combined '
                            'quantities' % (order_id,))
                else:
                    accepted = reserve(remaining, combined)
                    seen[order_id] = (key, accepted)
                results.append({'id': order_id, 'accepted': accepted})
        except ValueError:
            raise
        # Commit only after every order in the batch succeeded.
        self._stock = remaining
        self._seen = seen
        return {'remaining': dict(remaining), 'results': results}


def allocate(stock, orders):
    """Allocate orders against a fresh inventory built from ``stock``.

    Equivalent to ``Inventory(stock).allocate(orders)``; kept as the original
    function API, so it retains all prior behavior including per-call only ID
    replay memory.
    """
    return Inventory(stock).allocate(orders)
