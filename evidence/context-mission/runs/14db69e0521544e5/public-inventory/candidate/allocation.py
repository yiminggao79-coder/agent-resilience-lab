from lines import quantities
from warehouse import reserve


class Inventory:
    """An allocation state whose stock and order-ID memory persist across calls."""

    def __init__(self, stock):
        self._stock = dict(stock)
        self._seen = {}

    def allocate(self, orders):
        """Allocate one batch atomically, rolling back on an ID conflict."""
        previous_stock = dict(self._stock)
        previous_seen = dict(self._seen)
        results = []
        try:
            for order in orders:
                order_id = order['id']
                order_quantities = quantities(order['items'])
                if order_id in self._seen:
                    original_quantities, accepted = self._seen[order_id]
                    if order_quantities != original_quantities:
                        raise ValueError(
                            'order id %r reused with different quantities'
                            % (order_id,)
                        )
                else:
                    accepted = reserve(self._stock, order_quantities)
                    self._seen[order_id] = (order_quantities, accepted)
                results.append({'id': order_id, 'accepted': accepted})
        except ValueError:
            self._stock = previous_stock
            self._seen = previous_seen
            raise
        return {'remaining': dict(self._stock), 'results': results}

    def snapshot(self):
        """Return a detached copy of the current stock."""
        return dict(self._stock)


def allocate(stock, orders):
    """Allocate using a fresh inventory, preserving the stateless function API."""
    return Inventory(stock).allocate(orders)
