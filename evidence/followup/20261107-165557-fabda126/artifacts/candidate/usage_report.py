"""Public utilization reporting API."""

from ranges import clip_interval, coalesce
from timecodec import instant


def report_usage(records, window_start, window_end, include_idle=False):
    """Summarize device busy time within the half-open report window.

    ``include_idle=False`` (default) returns the historical shape:
    ``{"device", "busy_seconds", "span_count"}`` per device.

    ``include_idle=True`` additionally reports ``idle_seconds``, defined as the
    full window duration minus the device's union busy seconds. The window is
    measured as an instant difference, so it is correct across UTC offsets and
    day boundaries.
    """
    left, right = instant(window_start), instant(window_end)
    grouped = {}
    for record in records:
        interval = clip_interval(instant(record["start"]), instant(record["end"]), left, right)
        if interval is not None:
            grouped.setdefault(record["device"], []).append(interval)
    window_seconds = int((right - left).total_seconds())
    result = []
    for device in sorted(grouped):
        spans = coalesce(grouped[device])
        seconds = sum(int((end - start).total_seconds()) for start, end in spans)
        item = {"device": device, "busy_seconds": seconds, "span_count": len(spans)}
        if include_idle:
            item["idle_seconds"] = window_seconds - seconds
        result.append(item)
    return result
