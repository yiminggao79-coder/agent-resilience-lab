"""Convert parsed members into a compact list of byte spans."""


def resolve_members(members, size):
    """Turn parsed members into satisfiable, inclusive [start, end] spans."""
    spans = []
    for first, last in members:
        if size <= 0:
            # An empty asset cannot satisfy any member.
            continue
        if first is None:
            # "-n" suffix: last n bytes, discarded when n is zero.
            if last is None or last <= 0:
                continue
            start = max(0, size - last)
            end = size - 1
        else:
            if first > size - 1:
                continue
            start = first
            if last is None:
                end = size - 1
            else:
                if last < first:
                    continue
                end = min(last, size - 1)
        if start > end:
            continue
        spans.append([start, end])
    return spans


def coalesce(spans):
    """Sort spans by start and merge every overlap or adjacency."""
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 1:
            if end > merged[-1][1]:
                merged[-1][1] = end
        else:
            merged.append([start, end])
    return merged
