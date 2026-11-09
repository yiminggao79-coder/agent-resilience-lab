"""Parse the range syntax used by our transfer endpoint."""

_DIGITS = frozenset("0123456789")


def _is_digits(part):
    return all(c in _DIGITS for c in part)


def parse_header(header):
    """Return a list of (first, last) pairs, or None if the header is invalid.

    ``first``/``last`` are ``None`` when absent. Leading zeroes are allowed.
    """
    if header is None:
        return None
    if not isinstance(header, str):
        return None
    header = header.strip()
    if not header.startswith("bytes="):
        return None
    rest = header[len("bytes="):]
    if not rest:
        return None
    members = []
    for member in rest.split(","):
        member = member.strip()
        if member.count("-") != 1:
            return None
        first, last = member.split("-")
        if not first and not last:
            return None
        if first and not _is_digits(first):
            return None
        if last and not _is_digits(last):
            return None
        members.append((int(first) if first else None,
                        int(last) if last else None))
    return members
