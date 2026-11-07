"""Atomic edit groups with sequential group visibility."""
from records import canonical, ordered
from requests import limits, blackout_rows, rejection

def apply_groups(existing, groups, capacities, blocks=()):
    current = canonical(existing)
    room_limits, blocked = limits(capacities), blackout_rows(blocks)
    prepared, identities = [], set()
    for group in groups:
        identity = group.get('id')
        if not isinstance(identity, str) or not identity.strip() or identity in identities:
            raise ValueError('group ids must be unique nonempty strings')
        identities.add(identity)
        remove = group.get('remove', [])
        if not isinstance(remove, list) or any(not isinstance(v, str) for v in remove) or len(set(remove)) != len(remove):
            raise ValueError('remove must contain unique ids')
        # Keep duplicate additions visible as a rejected group, not an invalid request.
        added = [canonical([row])[0] for row in group.get('add', [])]
        prepared.append((identity, remove[:], added))
    accepted, rejected = [], []
    for identity, remove, added in prepared:
        old_ids = {row['id'] for row in current}
        missing = next((v for v in remove if v not in old_ids), None)
        reason = 'missing:' + missing if missing is not None else None
        candidate = [row for row in current if row['id'] not in set(remove)]
        seen = {row['id'] for row in candidate}
        for item in added:
            if reason is None and item['id'] in seen:
                reason = 'duplicate:' + item['id']
            seen.add(item['id'])
        if reason is None:
            reason = rejection(added, candidate + added, room_limits, blocked)
        if reason:
            rejected.append({'id': identity, 'reason': reason})
        else:
            accepted.append(identity)
            current = candidate + added
    return {'accepted': accepted, 'rejected': rejected, 'schedule': ordered(current)}
