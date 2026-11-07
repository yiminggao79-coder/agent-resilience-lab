def active_intervals(records, start, stop):
    for record in records:
        if record.get("cancelled", False):
            continue
        left = max(start, record["start"])
        right = record["end"]
        if left < right and left < stop:
            yield record["room"], left, right

