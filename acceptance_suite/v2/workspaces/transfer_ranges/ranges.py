"""Public range selection API."""

from range_syntax import parse_header
from range_sets import coalesce, resolve_members


def _result(status, spans):
    return {"status": status, "ranges": spans,
            "selected_bytes": sum(end - start + 1 for start, end in spans)}


def select_ranges(header, size, max_ranges):
    members = parse_header(header)
    full = [[0, size - 1]] if size else []
    if members is None or len(members) > max_ranges:
        return _result(200, full)
    spans = coalesce(resolve_members(members, size))
    return _result(206 if spans else 416, spans)
