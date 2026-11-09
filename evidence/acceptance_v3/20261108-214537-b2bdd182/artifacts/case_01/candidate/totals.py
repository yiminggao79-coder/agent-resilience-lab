def union_minutes(spans):
    """Total length covered by the union of half-open [left, right) spans."""
    total = 0
    current_left = current_right = None
    for left, right in sorted(spans):
        if current_right is None:
            current_left, current_right = left, right
        elif left <= current_right:
            current_right = max(current_right, right)
        else:
            total += current_right - current_left
            current_left, current_right = left, right
    if current_right is not None:
        total += current_right - current_left
    return total


def room_totals(intervals):
    groups = {}
    for room, left, right in intervals:
        if left < right:
            groups.setdefault(room, []).append((left, right))
    result = []
    for room, spans in groups.items():
        points = []
        for left, right in spans:
            points.append((left, 1))
            points.append((right, -1))
        # At the same minute, ends (-1) are processed before starts (+1)
        # because half-open intervals do not overlap at the boundary.
        points.sort(key=lambda point: (point[0], point[1]))
        active = peak = 0
        for _, change in points:
            active += change
            peak = max(peak, active)
        result.append({"room": room,
                       "busy_minutes": union_minutes(spans),
                       "peak": peak})
    result.sort(key=lambda entry: entry["room"])
    return result


