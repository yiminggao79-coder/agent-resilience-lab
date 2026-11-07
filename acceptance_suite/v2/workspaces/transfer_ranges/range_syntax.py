"""Parse the range syntax used by our transfer endpoint."""


def parse_header(header):
    if header is None:
        return None
    header = header.strip()
    if not header.startswith("bytes="):
        return None
    members = []
    for member in header[6:].split(","):
        member = member.strip()
        if member.count("-") != 1:
            return None
        first, last = member.split("-")
        if not first and not last:
            return None
        if any(part and any(c not in "0123456789" for c in part)
               for part in (first, last)):
            return None
        members.append((int(first) if first else None,
                        int(last) if last else None))
    return members
