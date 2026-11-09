from lines import quantities
from warehouse import reserve


def _identity(combined):
    """Order-independent identity of a combined quantity map."""
    return tuple(sorted(combined.items(), key=lambda pair: pair[0]))


def allocate(stock, orders):
    remaining = dict(stock)
    results = []
    seen = {}
    for order in orders:
        combined = quantities(order['items'])
        key = order['id']
        fingerprint = _identity(combined)
        if key in seen:
            previous_identity, accepted = seen[key]
            if previous_identity != fingerprint:
                raise ValueError(
                    'order id %r reused with different quantities' % (key,))
        else:
            accepted = reserve(remaining, combined)
            seen[key] = (fingerprint, accepted)
        results.append({'id': key, 'accepted': accepted})
    return {'remaining': remaining, 'results': results}
