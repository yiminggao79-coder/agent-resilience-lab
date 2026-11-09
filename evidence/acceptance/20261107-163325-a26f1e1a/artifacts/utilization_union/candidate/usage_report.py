"""Public utilization reporting API."""

from ranges import clip_interval, coalesce
from timecodec import instant


def report_usage(records, window_start, window_end):
    left, right = instant(window_start), instant(window_end)
    grouped = {}
    for record in records:
        interval = clip_interval(instant(record["start"]), instant(record["end"]), left, right)
        if interval is not None:
            grouped.setdefault(record["device"], []).append(interval)
    result = []
    for device in sorted(grouped):
        spans = coalesce(grouped[device])
        seconds = sum(int((end - start).total_seconds()) for start, end in spans)
        result.append({"device": device, "busy_seconds": seconds, "span_count": len(spans)})
    return result
