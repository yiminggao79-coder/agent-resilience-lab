def room_totals(intervals):
    groups = {}
    for room, left, right in intervals:
        groups.setdefault(room, []).append((left, right))
    result = []
    for room, spans in groups.items():
        points = [(left, 1) for left, _ in spans]
        points += [(right, -1) for _, right in spans]
        points.sort(key=lambda point: (point[0], -point[1]))
        active = peak = 0
        for _, change in points:
            active += change
            peak = max(peak, active)
        result.append({"room": room, "busy_minutes": sum(b-a for a,b in spans), "peak": peak})
    return result

