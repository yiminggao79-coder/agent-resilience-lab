from lines import quantities
from warehouse import reserve


class Inventory:
    def __init__(self, stock):
        self._stock = dict(stock)
        self._decisions = {}

    def allocate(self, orders):
        # Work on copies so a conflicting ID cannot partially commit this batch.
        remaining = dict(self._stock)
        decisions = dict(self._decisions)
        results = []

        for order in orders:
            order_id = order['id']
            wanted = quantities(order['items'])
            if order_id in decisions:
                seen, accepted = decisions[order_id]
                if seen != wanted:
                    raise ValueError('conflicting quantities for order id %r' % (order_id,))
            else:
                accepted = reserve(remaining, wanted)
                decisions[order_id] = (wanted, accepted)
            results.append({'id': order_id, 'accepted': accepted})

        self._stock = remaining
        self._decisions = decisions
        return {'remaining': dict(remaining), 'results': results}

    def snapshot(self):
        return dict(self._stock)


def allocate(stock, orders):
    return Inventory(stock).allocate(orders)
