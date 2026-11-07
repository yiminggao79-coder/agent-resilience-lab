from lines import quantities
from warehouse import reserve


def allocate(stock, orders):
    results = []
    for order in orders:
        accepted = reserve(stock, quantities(order['items']))
        results.append({'id': order['id'], 'accepted': accepted})
    return {'remaining': stock, 'results': results}
