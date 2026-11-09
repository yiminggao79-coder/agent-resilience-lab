"""Priority-ordered room allocation over one-day bookings.

Interval rule: bookings and blocks are half-open [start, end), so merely
touching endpoints never counts as an overlap.
"""
from clockwork import day_key, minute
from records import booking, canonical, ordered, positive, room_key

DEFAULT_CAPACITY = 1
_FIELDS = ('id', 'room', 'day', 'start', 'end', 'attendees')


def _priority(value):
    if type(value) is not int:
        raise ValueError("priority must be an integer")
    return value


def _request(row):
    item = booking(row)
    item['priority'] = _priority(row.get('priority', 0))
    return item


def _block(row):
    if not isinstance(row, dict):
        raise ValueError("block must be a dictionary")
    try:
        result = dict(room=room_key(row['room']), day=day_key(row['day']),
                      start=minute(row['start']), end=minute(row['end']))
    except KeyError as exc:
        raise ValueError("missing block field: " + str(exc)) from None
    if result['start'] >= result['end']:
        raise ValueError("block must end after it starts")
    return result


def _capacities(source):
    if not isinstance(source, dict):
        raise ValueError("capacities must be a dictionary")
    result = {}
    for name, value in source.items():
        key = room_key(name)
        if key in result:
            raise ValueError("duplicate capacity room: " + key)
        result[key] = positive(value, 'capacity')
    return result


def _touches(left, right):
    """True when two entries of the same room/day overlap under half-open rules."""
    return (left['room'] == right['room'] and left['day'] == right['day']
            and left['start'] < right['end'] and right['start'] < left['end'])


def _same_slot(left, right):
    return left['room'] == right['room'] and left['day'] == right['day']


def _fits(candidate, booked, capacity):
    """Whether adding `candidate` keeps simultaneous attendees within `capacity`."""
    if candidate['attendees'] > capacity:
        return False
    # Load only changes at neighbouring starts/ends strictly inside the request,
    # so checking each such boundary covers every sub-interval exactly once.
    points = {candidate['start']}
    for other in booked:
        if _same_slot(candidate, other):
            for point in (other['start'], other['end']):
                if candidate['start'] <= point < candidate['end']:
                    points.add(point)
    for point in points:
        total = sum(other['attendees'] for other in booked
                    if _same_slot(candidate, other)
                    and other['start'] <= point < other['end'])
        if total + candidate['attendees'] > capacity:
            return False
    return True


def allocate(existing, requests, capacities, blocks=()):
    # Validate and normalize every input before any allocation decision is made.
    booked = canonical(existing)
    candidates = [_request(row) for row in requests]
    limits = _capacities(capacities)
    stopped = [_block(row) for row in blocks]

    seen = set(item['id'] for item in booked)
    for item in candidates:
        if item['id'] in seen:
            raise ValueError("duplicate booking id")
        seen.add(item['id'])

    # Higher priority first; sort is stable, so ties keep request input order.
    queue = sorted(enumerate(candidates), key=lambda pair: (-pair[1]['priority'], pair[0]))

    accepted, rejected = [], []
    for _, item in queue:
        room, day, start, end = item['room'], item['day'], item['start'], item['end']
        window = dict(room=room, day=day, start=start, end=end)
        if any(_touches(window, block) for block in stopped):
            rejected.append({'id': item['id'], 'reason': 'blocked'})
            continue
        if not _fits(item, booked, limits.get(room, DEFAULT_CAPACITY)):
            rejected.append({'id': item['id'], 'reason': 'capacity'})
            continue
        accepted.append(item['id'])
        booked.append(item)

    schedule = [{field: item[field] for field in _FIELDS} for item in booked]
    return {'accepted': accepted, 'rejected': rejected, 'schedule': ordered(schedule)}
