def active_intervals(records, start, stop):
    """Yield (room, left, right) for each reservation clipped to [start, stop).

    Cancelled and zero-length reservations are skipped. A clipped span is only
    yielded when it has positive length, so rooms with no overlap are omitted.
    The input records are never modified.
    """
    for record in records:
        if record.get("cancelled", False):
            continue
        left = max(start, record["start"])
        right = min(stop, record["end"])
        if left < right:
            yield record["room"], left, right


