"""Small JSON-ready daily summaries."""
from clockwork import day_key
from records import canonical, ordered

def day_report(rows, day):
    day_key(day)
    grouped = {}
    for row in ordered(canonical(rows)):
        if row['day'] != day:
            continue
        group = grouped.setdefault(row['room'], {'minutes': 0, 'ids': []})
        group['minutes'] += row['end'] - row['start']
        group['ids'].append(row['id'])
    return {'day': day, 'rooms': grouped}
