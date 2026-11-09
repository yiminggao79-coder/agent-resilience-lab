"""Owned carton-type records with integer scalar capacities."""
from catalog import name, integer


def validate(rows):
    if not isinstance(rows, list):
        raise ValueError('boxes must be a list')
    output = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('box must be a dictionary')
        try:
            code = name(row['code'])
            max_weight = integer(row['max_weight'])
            max_volume = integer(row['max_volume'])
        except KeyError:
            raise ValueError('missing box field') from None
        if code in seen:
            raise ValueError('duplicate box code')
        seen.add(code)
        output.append(dict(code=code, max_weight=max_weight, max_volume=max_volume))
    return sorted(output, key=lambda box: (box['max_volume'], box['max_weight'], box['code']))
