"""Deterministic scalar first-fit carton packing."""
from catalog import products
from orders import quantities
from boxes import validate as validate_boxes
from policies import validate as validate_policy


def _fits(weight, volume, box):
    return weight <= box['max_weight'] and volume <= box['max_volume']


def pack(catalog, lines, boxes, policy=None):
    entries = products(catalog)
    counts = quantities(lines, entries)
    types = validate_boxes(boxes)
    rules = validate_policy(policy, entries, types)
    incompatible = rules['incompatible']
    max_units = rules['max_units']
    fixed_box = rules['fixed_box']
    units = []
    for sku, quantity in counts.items():
        unit = entries[sku]
        for _ in range(quantity):
            units.append((unit['weight'], unit['volume'], sku))
    units.sort(key=lambda unit: (-unit[0], -unit[1], unit[2]))
    cartons = []
    for weight, volume, sku in units:
        for carton in cartons:
            box = carton['box']
            if sku in fixed_box and fixed_box[sku] != box['code']:
                continue
            if max_units is not None and carton['units'] + 1 > max_units:
                continue
            if any(other in incompatible.get(sku, ()) for other in carton['items']):
                continue
            if (carton['weight'] + weight <= box['max_weight'] and
                    carton['volume'] + volume <= box['max_volume']):
                carton['items'][sku] = carton['items'].get(sku, 0) + 1
                carton['weight'] += weight
                carton['volume'] += volume
                carton['units'] += 1
                break
        else:
            for box in types:
                if sku in fixed_box and fixed_box[sku] != box['code']:
                    continue
                if _fits(weight, volume, box):
                    cartons.append({'box': box, 'items': {sku: 1},
                                    'weight': weight, 'volume': volume,
                                    'units': 1})
                    break
            else:
                raise ValueError('unit does not fit any box')
    return [{'box': carton['box']['code'],
             'items': [{'sku': sku, 'quantity': quantity}
                       for sku, quantity in sorted(carton['items'].items())],
             'weight': carton['weight'], 'volume': carton['volume']}
            for carton in cartons]
