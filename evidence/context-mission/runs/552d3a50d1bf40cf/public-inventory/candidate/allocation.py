from lines import quantities
from warehouse import reserve


def allocate(stock, orders):
    remaining = {sku: qty for sku, qty in stock.items()}
    results = []
    for order in orders:
        accepted = reserve(remaining, quantities(order['items']))
        results.append({'id': order['id'], 'accepted': accepted})
    return {'remaining': remaining, 'results': results}
