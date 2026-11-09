from lines import quantities
from warehouse import reserve


def allocate(stock, orders):
    remaining = dict(stock)
    results = []
    for order in orders:
        accepted = reserve(remaining, quantities(order['items']))
        results.append({'id': order['id'], 'accepted': accepted})
    return {'remaining': remaining, 'results': results}
