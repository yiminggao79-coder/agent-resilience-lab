from labelkit.normalize import canonical_keys

def normalize(values, exclude=None):
    """Canonicalize values, optionally omitting canonical keys in exclude.

    ``exclude`` strings are canonicalized with the same rules as ``values``
    (trim Unicode whitespace, casefold, discard empties, deduplicate). Keys
    matching any excluded key are omitted from the output. ``None`` means no
    exclusions. Neither input is mutated.
    """
    keys = canonical_keys(values)
    if exclude is None:
        return keys
    excluded = set(canonical_keys(exclude))
    return [key for key in keys if key not in excluded]
