from lines import quantities
from warehouse import reserve


class Inventory:
    """Inventory holding stock and idempotent order-id memory across batches.

    ``stock`` is copied at construction time, so the caller's mapping is never
    mutated. ``allocate(orders)`` applies the same rules as the module-level
    function, but remaining stock and remembered order ids persist between
    calls. Each batch is atomic with respect to ``ValueError``: when a reused
    id carries different combined quantities, stock and replay memory are
    rolled back to their state before the batch started, while earlier
    successful batches stay committed.
    """

    def __init__(self, stock):
        self._stock = dict(stock)
        # order id -> (combined quantities, accepted) for its first decision.
        self._decisions = {}

    def snapshot(self):
        """Return a detached copy of the current stock."""
        return dict(self._stock)

    def allocate(self, orders):
        """Allocate a batch of orders and return ``remaining`` and ``results``."""
        # Work on copies so a mid-batch conflict can be rolled back wholesale.
        stock = dict(self._stock)
        decisions = dict(self._decisions)
        results = []
        for order in orders:
            order_id = order['id']
            combined = quantities(order['items'])
            if order_id in decisions:
                known_combined, accepted = decisions[order_id]
                if known_combined != combined:
                    raise ValueError(
                        'order id {!r} reused with different quantities'.format(order_id))
            else:
                accepted = reserve(stock, combined)
                decisions[order_id] = (combined, accepted)
            results.append({'id': order_id, 'accepted': accepted})
        # Commit only after the whole batch succeeded.
        self._stock = stock
        self._decisions = decisions
        return {'remaining': dict(stock), 'results': results}


def allocate(stock, orders):
    """Allocate stock to orders in arrival order using a throwaway inventory.

    Orders reusing an earlier ``id`` within this call are idempotent: when the
    combined SKU quantities match the first order seen with that id, the
    earlier accepted/rejected decision is replayed without consuming stock
    again. Reusing an id with different combined quantities raises
    ``ValueError``; the caller's ``stock`` is never mutated, so inputs are
    unchanged even on that error. Memory does not persist between calls.
    """
    return Inventory(stock).allocate(orders)
