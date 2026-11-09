"""Stock-aware whole-order dispatch."""
from catalog import name, products
from orders import quantities
from boxes import validate as validate_boxes
from policies import validate as validate_policy
from manifest import constrained


def _stock_remaining(stock, entries):
    if not isinstance(stock, dict):
        raise ValueError('stock must be a dictionary')
    remaining = {sku: 0 for sku in entries}
    for raw_sku, quantity in stock.items():
        sku = name(raw_sku)
        if sku not in entries:
            raise ValueError('unknown stock sku')
        if type(quantity) is not int or quantity < 0:
            raise ValueError('invalid stock quantity')
        remaining[sku] = quantity
    return dict(sorted(remaining.items()))


def _requests(requests, entries):
    if not isinstance(requests, list):
        raise ValueError('requests must be a list')
    output = []
    ids = set()
    for row in requests:
        if not isinstance(row, dict):
            raise ValueError('request must be a dictionary')
        try:
            request_id = name(row['id'])
            destination = name(row['destination'])
            lines = row['lines']
        except KeyError:
            raise ValueError('missing request field') from None
        if request_id in ids:
            raise ValueError('duplicate request id')
        ids.add(request_id)
        priority = row.get('priority', 0)
        if type(priority) is not int:
            raise ValueError('invalid priority')
        counts = quantities(lines, entries)
        output.append({'id': request_id, 'destination': destination,
                       'priority': priority, 'lines': lines, 'counts': counts})
    return output


def allocate(catalog, stock, requests, boxes, policy=None):
    entries = products(catalog)
    remaining = _stock_remaining(stock, entries)
    types = validate_boxes(boxes)
    validate_policy(policy, entries, types)
    pending = _requests(requests, entries)

    order = sorted(range(len(pending)),
                   key=lambda index: (-pending[index]['priority'], index))
    accepted = []
    rejected = []
    totals = {'units': 0, 'cartons': 0, 'weight': 0, 'volume': 0}

    for index in order:
        request = pending[index]
        if any(quantity > remaining[sku]
               for sku, quantity in request['counts'].items()):
            rejected.append({'id': request['id'], 'reason': 'stock'})
            continue
        try:
            report = constrained(catalog, request['lines'], boxes, policy)
        except ValueError:
            rejected.append({'id': request['id'], 'reason': 'unpackable'})
            continue

        for sku, quantity in request['counts'].items():
            remaining[sku] -= quantity
        accepted.append({'id': request['id'],
                         'destination': request['destination'],
                         'manifest': report})
        totals['units'] += report['totals']['units']
        totals['cartons'] += len(report['cartons'])
        totals['weight'] += report['totals']['weight']
        totals['volume'] += report['totals']['volume']

    return {'accepted': accepted, 'rejected': rejected,
            'remaining': remaining, 'totals': totals}
