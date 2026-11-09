"""Validation and compilation of optional co-packing restrictions."""
from catalog import name, integer
from catalog import products as _products
from boxes import validate as _boxes_validate

_ALLOWED = {'incompatible', 'max_units', 'fixed_box'}


def _entries(catalog):
    return catalog if isinstance(catalog, dict) else _products(catalog)


def _codes(boxes):
    if isinstance(boxes, dict):
        return set(boxes)
    if isinstance(boxes, (set, frozenset)):
        return set(boxes)
    return {box['code'] for box in _boxes_validate(boxes)}


def validate(policy, catalog, boxes):
    """Validate a policy and return a fresh compiled restriction mapping."""
    entries = _entries(catalog)
    codes = _codes(boxes)
    if policy is None:
        policy = {}
    if not isinstance(policy, dict):
        raise ValueError('policy must be a dictionary')
    if set(policy) - _ALLOWED:
        raise ValueError('unknown policy field')
    pairs = policy.get('incompatible', [])
    if not isinstance(pairs, list):
        raise ValueError('incompatible must be a list')
    incompatible = {}
    for pair in pairs:
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError('incompatible pair must contain two skus')
        left, right = name(pair[0]), name(pair[1])
        if left == right:
            raise ValueError('incompatible skus must differ')
        if left not in entries or right not in entries:
            raise ValueError('unknown sku')
        incompatible.setdefault(left, set()).add(right)
        incompatible.setdefault(right, set()).add(left)
    max_units = policy.get('max_units')
    if max_units is not None:
        max_units = integer(max_units)
    fixed = policy.get('fixed_box', {})
    if not isinstance(fixed, dict):
        raise ValueError('fixed_box must be a dictionary')
    fixed_box = {}
    for sku, code in fixed.items():
        sku, code = name(sku), name(code)
        if sku not in entries:
            raise ValueError('unknown sku')
        if code not in codes:
            raise ValueError('unknown box code')
        if sku in fixed_box:
            raise ValueError('duplicate fixed_box sku')
        fixed_box[sku] = code
    return {'incompatible': {sku: frozenset(others)
                             for sku, others in incompatible.items()},
            'max_units': max_units,
            'fixed_box': fixed_box}


def compile(policy, catalog, boxes):
    return validate(policy, catalog, boxes)


compile_policy = validate
