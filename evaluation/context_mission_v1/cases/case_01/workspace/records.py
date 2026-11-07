"""Canonical records never share mutable state with callers."""
from clockwork import day_key, minute

def positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(name + " must be a positive integer")
    return value

def room_key(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("room must be nonempty text")
    return value.strip().casefold()

def booking(row):
    if not isinstance(row, dict):
        raise ValueError("booking must be a dictionary")
    try:
        identity = row['id']
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("id must be nonempty text")
        result = dict(id=identity, room=room_key(row['room']), day=day_key(row['day']),
                      start=minute(row['start']), end=minute(row['end']),
                      attendees=positive(row.get('attendees', 1), 'attendees'))
    except KeyError as exc:
        raise ValueError("missing booking field: " + str(exc)) from None
    if result['start'] >= result['end']:
        raise ValueError("booking must end after it starts")
    return result

def canonical(rows):
    result, seen = [], set()
    for row in rows:
        item = booking(row)
        if item['id'] in seen:
            raise ValueError("duplicate booking id")
        seen.add(item['id'])
        result.append(item)
    return result

def ordered(rows):
    return sorted(rows, key=lambda r: (r['room'], r['day'], r['start'], r['id']))
