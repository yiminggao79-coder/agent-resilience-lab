def room_totals(intervals):
    groups = {}
    for room, left, right in intervals:
        events = groups.setdefault(room, {})
        events[left] = events.get(left, 0) + 1
        events[right] = events.get(right, 0) - 1
    result = []
    for room in sorted(groups):
        events = groups[room]
        active = peak = busy = 0
        previous = None
        for when in sorted(events):
            if previous is not None and active:
                busy += when - previous
            active += events[when]
            peak = max(peak, active)
            previous = when
        result.append({"room": room, "busy_minutes": busy, "peak": peak})
    return result

