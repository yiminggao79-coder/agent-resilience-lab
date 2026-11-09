"""Exact rational helpers for musical tick arithmetic."""

_MISSING = object()


def _positive_integer(value, label):
    if type(value) is not int or value <= 0:
        raise ValueError('invalid %s' % label)
    return value


def _pair(numerator, denominator):
    return (_positive_integer(numerator, 'ratio numerator'),
            _positive_integer(denominator, 'ratio denominator'))


def ratio(value=None, denominator=_MISSING):
    """Return a validated ``(numerator, denominator)`` ratio.

    ``None`` is the identity ratio. A single two-item list/tuple is also
    accepted, as are separate numerator and denominator arguments.
    """
    if denominator is not _MISSING:
        return _pair(value, denominator)
    if value is None:
        return (1, 1)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return _pair(value[0], value[1])
    raise ValueError('invalid ratio')


def scale(value, factor=_MISSING, denominator=_MISSING):
    """Scale an exact nonnegative tick without rounding.

    ``factor`` may be a stretch pair, ``None`` for identity, or a numerator
    when ``denominator`` is supplied separately. A fractional result raises
    ``ValueError``.
    """
    if type(value) is not int or value < 0:
        raise ValueError('invalid tick')
    if denominator is not _MISSING:
        if factor is _MISSING:
            raise ValueError('invalid ratio')
        numerator, denominator = _pair(factor, denominator)
    else:
        numerator, denominator = ratio(factor)
    product = value * numerator
    if product % denominator:
        raise ValueError('nonintegral tick')
    return product // denominator
