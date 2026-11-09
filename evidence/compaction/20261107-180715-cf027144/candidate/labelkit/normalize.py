def canonical_keys(values):
    """Trim, casefold and deduplicate strings, preserving first-occurrence order.

    Each value is trimmed of surrounding Unicode whitespace and casefolded.
    Values yielding an empty canonical result are discarded. Duplicates are
    removed keeping the first occurrence. The input is never mutated.
    """
    seen = set()
    result = []
    for value in values:
        key = value.strip().casefold()
        if not key:
            continue
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result
