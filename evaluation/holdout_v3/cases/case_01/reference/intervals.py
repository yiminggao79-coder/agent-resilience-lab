def active_intervals(records, start, stop):
    for record in records:
        if record.get("cancelled", False):
            continue
        left = max(start, record["start"])
        right = min(stop, record["end"])
        if left < right:
            yield record["room"], left, right

