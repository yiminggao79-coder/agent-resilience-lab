"""Conflict analysis; intervals are represented in minutes."""
from records import canonical

def overlap(left, right):
    return (left['room'] == right['room'] and left['day'] == right['day']
            and left['start'] <= right['end'] and right['start'] <= left['end'])

def conflicts(rows):
    entries = canonical(rows)
    pairs = []
    for index, left in enumerate(entries):
        for right in entries[index + 1:]:
            if overlap(left, right):
                pairs.append(sorted([left['id'], right['id']]))
    return sorted(pairs)
