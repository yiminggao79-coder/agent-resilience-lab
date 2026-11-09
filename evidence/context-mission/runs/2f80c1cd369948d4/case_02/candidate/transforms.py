"""Exact, detached note transformations."""

from pitch import transpose as transpose_pitch
from rhythm import ratio, scale
from score import canonical


def _integer(value, label):
    if type(value) is not int:
        raise ValueError('invalid %s' % label)
    return value


def apply(events, transpose=0, stretch=None, shift=0, reverse=False):
    """Return a canonically ordered, exactly transformed copy of ``events``."""
    transpose = _integer(transpose, 'transpose')
    shift = _integer(shift, 'shift')
    if type(reverse) is not bool:
        raise ValueError('invalid reverse')
    numerator, denominator = ratio(stretch)

    notes = canonical(events)
    original_end = max((note['start'] + note['duration'] for note in notes),
                       default=0)
    transformed = []
    for note in notes:
        start = note['start']
        duration = note['duration']
        if reverse:
            start = original_end - start - duration
        start = scale(start, (numerator, denominator))
        duration = scale(duration, (numerator, denominator))
        start += shift
        if start < 0:
            raise ValueError('negative start')
        transformed.append(dict(
            id=note['id'],
            start=start,
            duration=duration,
            pitch=transpose_pitch(note['pitch'], transpose),
            velocity=note['velocity'],
        ))
    return canonical(transformed)
