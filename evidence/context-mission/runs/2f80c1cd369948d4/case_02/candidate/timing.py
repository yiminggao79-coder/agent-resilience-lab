"""Exact conversion between musical ticks and elapsed microseconds."""

from fractions import Fraction


def _integer(value, label, minimum):
    if type(value) is not int or value < minimum:
        raise ValueError('invalid %s' % label)
    return value


def validate(tempo, ticks_per_beat=480):
    """Validate and copy a complete tempo history.

    Every entry is checked, even entries later than any note being rendered.
    """
    _integer(ticks_per_beat, 'ticks per beat', 1)
    if not isinstance(tempo, list) or not tempo:
        raise ValueError('invalid tempo')

    output = []
    previous_tick = None
    for entry in tempo:
        if not isinstance(entry, dict):
            raise ValueError('invalid tempo entry')
        try:
            tick = _integer(entry['tick'], 'tempo tick', 0)
            microseconds = _integer(
                entry['microseconds_per_beat'],
                'microseconds per beat',
                1,
            )
        except KeyError:
            raise ValueError('tempo field missing') from None

        if previous_tick is None:
            if tick != 0:
                raise ValueError('tempo must start at tick zero')
        elif tick <= previous_tick:
            raise ValueError('tempo ticks must strictly increase')
        previous_tick = tick
        output.append({
            'tick': tick,
            'microseconds_per_beat': microseconds,
        })
    return output


def _convert(tick, tempo, ticks_per_beat):
    target = _integer(tick, 'target tick', 0)
    total = Fraction(0, 1)
    for index, entry in enumerate(tempo):
        start = entry['tick']
        if start >= target:
            break
        end = (tempo[index + 1]['tick']
               if index + 1 < len(tempo) else target)
        if end > target:
            end = target
        total += Fraction(
            (end - start) * entry['microseconds_per_beat'],
            ticks_per_beat,
        )
    return total.numerator // total.denominator


def to_microseconds(tick, tempo, ticks_per_beat=480):
    """Convert a nonnegative tick using the accumulated exact tempo total."""
    normalized = validate(tempo, ticks_per_beat)
    return _convert(tick, normalized, ticks_per_beat)
