"""Daily summaries use union duration, not attendee time."""
from clockwork import day_key
from records import canonical, ordered

def day_report(rows, day):
    day_key(day)
    grouped = {}
    intervals = {}
    for row in ordered(canonical(rows)):
        if row['day'] == day:
            group = grouped.setdefault(row['room'], {'minutes': 0, 'ids': []})
            group['ids'].append(row['id'])
            intervals.setdefault(row['room'], []).append((row['start'], row['end']))
    for room, spans in intervals.items():
        start, end = spans[0]
        duration = 0
        for left, right in spans[1:]:
            if left <= end:
                end = max(end, right)
            else:
                duration += end - start
                start, end = left, right
        grouped[room]['minutes'] = duration + end - start
    return {'day': day, 'rooms': grouped}
