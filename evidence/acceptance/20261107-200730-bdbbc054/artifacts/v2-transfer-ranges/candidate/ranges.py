"""Public range selection API."""

from range_syntax import parse_header
from range_sets import coalesce, resolve_members


def _result(status, spans):
    return {"status": status, "ranges": spans,
            "selected_bytes": sum(end - start + 1 for start, end in spans)}


def select_ranges(header, size, max_ranges):
    """Compute the inclusive byte spans to send for an asset of ``size`` bytes."""
    full = [[0, size - 1]] if size else []
    members = parse_header(header)
    if members is None:
        return _result(200, full)
    spans = coalesce(resolve_members(members, size))
    if not spans:
        return _result(416, [])
    if len(spans) > max_ranges:
        return _result(200, full)
    return _result(206, spans)
