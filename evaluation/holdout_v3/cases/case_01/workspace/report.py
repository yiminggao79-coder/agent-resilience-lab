from intervals import active_intervals
from totals import room_totals


def summarize(records, start, stop):
    return room_totals(active_intervals(records, start, stop))

