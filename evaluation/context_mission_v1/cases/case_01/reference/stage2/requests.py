"""Deterministic admission against rooms and blocked intervals."""
from records import canonical, booking, ordered, positive, room_key
from schedule import overlap

def limits(capacities):
    result = {}
    for room, capacity in capacities.items():
        key = room_key(room)
        if key in result:
            raise ValueError('duplicate normalized room capacity')
        result[key] = positive(capacity, 'capacity')
    return result

def blackout_rows(blocks):
    return [booking(dict(block, id='@block-' + str(index), attendees=1))
            for index, block in enumerate(blocks)]

def rejection(added, occupants, capacities, blocks):
    if any(overlap(item, block) for item in added for block in blocks):
        return 'blocked'
    for item in added:
        peers = [peer for peer in occupants if overlap(item, peer)]
        checkpoints = {item['start']}
        checkpoints.update(peer['start'] for peer in peers if item['start'] <= peer['start'] < item['end'])
        for point in checkpoints:
            total = sum(peer['attendees'] for peer in peers if peer['start'] <= point < peer['end'])
            if total > capacities.get(item['room'], 1):
                return 'capacity'
    return None

def allocate(existing, requests, capacities, blocks=()):
    occupied = canonical(existing)
    proposed = canonical(requests)
    if {row['id'] for row in occupied} & {row['id'] for row in proposed}:
        raise ValueError('request id already exists')
    priorities = []
    for source, item in zip(requests, proposed):
        value = source.get('priority', 0)
        if type(value) is not int:
            raise ValueError('priority must be an integer')
        priorities.append((value, item))
    room_limits, blocked = limits(capacities), blackout_rows(blocks)
    accepted, rejected = [], []
    for _, item in sorted(priorities, key=lambda pair: -pair[0]):
        reason = rejection([item], occupied + [item], room_limits, blocked)
        if reason:
            rejected.append({'id': item['id'], 'reason': reason})
        else:
            accepted.append(item['id'])
            occupied.append(item)
    return {'accepted': accepted, 'rejected': rejected, 'schedule': ordered(occupied)}
