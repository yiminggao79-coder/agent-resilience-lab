"""Small JSON-ready daily summaries."""
from clockwork import day_key
from records import canonical, ordered

def union_minutes(intervals):
    """Total minutes covered by the union of half-open [start, end) intervals."""
    total, current_start, current_end = 0, None, None
    for start, end in sorted(intervals):
        if current_end is None or start > current_end:
            if current_end is not None:
                total += current_end - current_start
            current_start, current_end = start, end
        elif end > current_end:
            current_end = end
    if current_end is not None:
        total += current_end - current_start
    return total

def day_report(rows, day):
    day_key(day)
    grouped = {}
    spans = {}
    for row in ordered(canonical(rows)):
        if row['day'] != day:
            continue
        group = grouped.setdefault(row['room'], {'minutes': 0, 'ids': []})
        group['ids'].append(row['id'])
        spans.setdefault(row['room'], []).append((row['start'], row['end']))
    for room, intervals in spans.items():
        grouped[room]['minutes'] = union_minutes(intervals)
    return {'day': day, 'rooms': grouped}
