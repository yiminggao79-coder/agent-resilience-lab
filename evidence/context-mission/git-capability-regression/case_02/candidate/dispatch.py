"""Stock-aware, whole-request dispatch over the restricted packer."""
from boxes import validate as validate_boxes
from catalog import integer, name, products
from manifest import constrained
from orders import quantities
from policies import compile as compile_policy


def _stock(values, entries):
    if not isinstance(values, dict):
        raise ValueError('stock must be a dictionary')
    remaining = {sku: 0 for sku in entries}
    for sku, quantity in values.items():
        if not isinstance(sku, str) or sku not in entries:
            raise ValueError('unknown stock sku')
        remaining[sku] = integer(quantity, minimum=0)
    return {sku: remaining[sku] for sku in sorted(remaining)}


def _requests(records, entries):
    if not isinstance(records, list):
        raise ValueError('requests must be a list')
    output = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('request must be a dictionary')
        try:
            request_id = name(record['id'])
            destination = name(record['destination'])
            lines = record['lines']
        except KeyError:
            raise ValueError('missing request field') from None
        if request_id in seen:
            raise ValueError('duplicate request id')
        seen.add(request_id)

        priority = record.get('priority', 0)
        if type(priority) is not int:
            raise ValueError('invalid priority')
        counts = quantities(lines, entries)
        output.append({
            'id': request_id,
            'destination': destination,
            'priority': priority,
            'counts': counts,
        })
    return output


def allocate(catalog, stock, requests, boxes, policy=None):
    """Allocate requests in priority order and return an owned dispatch report."""
    entries = products(catalog)
    initial_stock = _stock(stock, entries)
    box_types = validate_boxes(boxes)
    compile_policy(policy, entries, box_types)
    parsed = _requests(requests, entries)

    remaining = dict(initial_stock)
    accepted = []
    rejected = []
    total_units = total_cartons = total_weight = total_volume = 0

    for request in sorted(parsed, key=lambda item: -item['priority']):
        counts = request['counts']
        if any(remaining[sku] < quantity for sku, quantity in counts.items()):
            rejected.append({'id': request['id'], 'reason': 'stock'})
            continue

        lines = [{'sku': sku, 'quantity': quantity}
                 for sku, quantity in counts.items()]
        try:
            report = constrained(catalog, lines, boxes, policy)
        except ValueError:
            rejected.append({'id': request['id'], 'reason': 'unpackable'})
            continue

        for sku, quantity in counts.items():
            remaining[sku] -= quantity
        accepted.append({
            'id': request['id'],
            'destination': request['destination'],
            'manifest': report,
        })
        total_units += report['totals']['units']
        total_cartons += len(report['cartons'])
        total_weight += report['totals']['weight']
        total_volume += report['totals']['volume']

    return {
        'accepted': accepted,
        'rejected': rejected,
        'remaining': {sku: remaining[sku] for sku in sorted(entries)},
        'totals': {
            'units': total_units,
            'cartons': total_cartons,
            'weight': total_weight,
            'volume': total_volume,
        },
    }
