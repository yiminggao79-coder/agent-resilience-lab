"""Half-open interval operations used by reports."""


def clip_interval(start, end, window_start, window_end):
    if end <= start:
        return None
    left, right = max(start, window_start), min(end, window_end)
    return (left, right) if left < right else None


def coalesce(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
