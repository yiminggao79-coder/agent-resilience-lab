"""Convert parsed members into a compact list of byte spans."""


def resolve_members(members, size):
    spans = []
    for first, last in members:
        start = 0 if first is None else first
        end = size - 1 if last is None else min(last, size - 1)
        if 0 <= start <= end:
            spans.append([start, end])
    return spans


def coalesce(spans):
    merged = []
    for start, end in sorted(spans):
        if merged and start < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged
