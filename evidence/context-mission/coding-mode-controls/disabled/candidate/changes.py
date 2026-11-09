"""Transactional group edits: remove and add reservations as one unit.

Each group is applied atomically: either every removal and addition of the
group takes effect, or the schedule is left exactly as it was before the
group. Groups are processed in input order, so the next group only sees the
changes accepted so far. Interval rules are the same half-open [start, end)
rules used elsewhere in this library.
"""
from records import booking, canonical, ordered, room_key
from requests import DEFAULT_CAPACITY, _FIELDS, _capacities, _block, _fits, _touches

_GROUP_KEYS = ('id', 'remove', 'add')


def _group_id(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("group id must be nonempty text")
    return value


def _removals(value):
    if not isinstance(value, list):
        raise ValueError("remove must be a list")
    result = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("remove entries must be nonempty text")
        if item in result:
            raise ValueError("remove must list distinct ids: " + item)
        result.append(item)
    return result


def _additions(value):
    if not isinstance(value, list):
        raise ValueError("add must be a list")
    return [booking(row) for row in value]


def _group(row):
    if not isinstance(row, dict):
        raise ValueError("group must be a dictionary")
    for key in row:
        if key not in _GROUP_KEYS:
            raise ValueError("unknown group field: " + str(key))
    if 'id' not in row:
        raise ValueError("missing group field: id")
    return {'id': _group_id(row['id']),
            'remove': _removals(row.get('remove', [])),
            'add': _additions(row.get('add', []))}


def _groups(rows):
    if not isinstance(rows, (list, tuple)):
        raise ValueError("groups must be a list")
    result = [_group(row) for row in rows]
    seen = set()
    for item in result:
        if item['id'] in seen:
            raise ValueError("duplicate group id: " + item['id'])
        seen.add(item['id'])
    return result


def _schedule(rows):
    return [{field: item[field] for field in _FIELDS} for item in ordered(rows)]


def _apply(schedule, group, stopped, limits):
    """Try one group against the current schedule.

    Returns (new_schedule, None) on success or (schedule, reason) on rejection.
    Nothing is mutated: a fresh list is built only for an accepted group.
    """
    removed = set(group['remove'])
    retained = []
    for item in schedule:
        if item['id'] in removed:
            removed.discard(item['id'])
        else:
            retained.append(item)
    if removed:
        # First missing id in remove order.
        missing = next(identity for identity in group['remove'] if identity in removed)
        return schedule, 'missing:' + missing

    known = set(item['id'] for item in retained)
    pending = None
    for item in group['add']:
        if item['id'] in known:
            pending = item
            break
        known.add(item['id'])
    if pending is not None:
        return schedule, 'duplicate:' + pending['id']

    for item in group['add']:
        window = dict(room=item['room'], day=item['day'], start=item['start'], end=item['end'])
        if any(_touches(window, block) for block in stopped):
            return schedule, 'blocked'

    result = list(retained)
    for item in group['add']:
        if not _fits(item, result, limits.get(item['room'], DEFAULT_CAPACITY)):
            return schedule, 'capacity'
        result.append(item)
    return result, None


def apply_groups(existing, groups, capacities, blocks=()):
    # Validate and normalize every caller input before any group is processed.
    schedule = canonical(existing)
    queue = _groups(groups)
    limits = _capacities(capacities)
    stopped = [_block(row) for row in blocks]

    accepted, rejected = [], []
    for group in queue:
        updated, reason = _apply(schedule, group, stopped, limits)
        if reason is None:
            accepted.append(group['id'])
            schedule = updated
        else:
            rejected.append({'id': group['id'], 'reason': reason})

    return {'accepted': accepted, 'rejected': rejected, 'schedule': _schedule(schedule)}
