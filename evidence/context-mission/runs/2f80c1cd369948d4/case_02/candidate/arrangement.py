"""Build clipped, repeated arrangements from transformed tracks."""

from score import by_track, identity, measure
from transforms import apply


_TRANSFORM_KEYS = frozenset(('transpose', 'stretch', 'shift', 'reverse'))
_MISSING = object()


def _positive_integer(value, label):
    if type(value) is not int or value < 1:
        raise ValueError('invalid %s' % label)
    return value


def _crop(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError('invalid crop')
    start, end = value
    if (type(start) is not int or type(end) is not int or
            start < 0 or end <= start):
        raise ValueError('invalid crop')
    return start, end


def build(tracks):
    """Return repeated, cropped tracks plus global and per-track metrics."""
    if not isinstance(tracks, list):
        raise ValueError('tracks must be a list')

    prepared = []
    seen = set()
    for track in tracks:
        if not isinstance(track, dict):
            raise ValueError('track must be a dictionary')
        try:
            track_id = identity(track['id'])
            events = track['events']
        except KeyError:
            raise ValueError('track field missing') from None
        if track_id in seen:
            raise ValueError('duplicate track identity')
        seen.add(track_id)

        repeat = _positive_integer(track.get('repeat', 1), 'repeat')

        period = track.get('period', _MISSING)
        if period is not _MISSING:
            period = _positive_integer(period, 'period')

        crop = track.get('crop', _MISSING)
        if crop is not _MISSING:
            crop = _crop(crop)
        else:
            crop = None

        options = track.get('transform', {})
        if not isinstance(options, dict):
            raise ValueError('invalid transform')
        if set(options) - _TRANSFORM_KEYS:
            raise ValueError('invalid transform option')

        transformed = apply(events, **options)
        if period is _MISSING:
            if not transformed and repeat > 1:
                raise ValueError('empty repeated score requires a period')
            period = max((note['start'] + note['duration']
                          for note in transformed), default=0)

        prepared.append((track_id, transformed, repeat, period, crop))

    notes = []
    for track_id, transformed, repeat, period, crop in prepared:
        for cycle in range(repeat):
            offset = cycle * period
            for note in transformed:
                start = note['start'] + offset
                end = start + note['duration']
                if crop is not None:
                    start = max(start, crop[0])
                    end = min(end, crop[1])
                    if start >= end:
                        continue
                notes.append(dict(
                    id=note['id'],
                    start=start,
                    duration=end - start,
                    pitch=note['pitch'],
                    velocity=note['velocity'],
                    track=track_id,
                    cycle=cycle,
                ))

    notes.sort(key=lambda note: (note['start'], note['pitch'], note['track'],
                                 note['cycle'], note['id']))
    return {
        'notes': notes,
        'metrics': measure(notes),
        'tracks': by_track(notes, [item[0] for item in prepared]),
    }
