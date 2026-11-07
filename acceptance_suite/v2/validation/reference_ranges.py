"""Independent acceptance reference; never copied into an evaluation workspace."""

import re


def select_ranges(header, size, max_ranges):
    full_ranges = [[0, size - 1]] if size else []

    def result(status, spans):
        return {"status": status, "ranges": spans,
                "selected_bytes": sum(b - a + 1 for a, b in spans)}

    if header is None:
        return result(200, full_ranges)
    header = header.strip()
    if not header.startswith("bytes="):
        return result(200, full_ranges)
    spans = []
    for raw_member in header[6:].split(","):
        member = raw_member.strip()
        match = re.fullmatch(r"([0-9]*)-([0-9]*)", member)
        if match is None or not any(match.groups()):
            return result(200, full_ranges)
        first, last = match.groups()
        if not size:
            continue
        if not first:
            count = int(last)
            if count:
                spans.append((max(0, size - count), size - 1))
        else:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
            if start <= end:
                spans.append((start, end))
    if not spans:
        return result(416, [])
    merged = []
    for start, end in sorted(spans):
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(end, merged[-1][1])
    return result(200, full_ranges) if len(merged) > max_ranges else result(206, merged)
