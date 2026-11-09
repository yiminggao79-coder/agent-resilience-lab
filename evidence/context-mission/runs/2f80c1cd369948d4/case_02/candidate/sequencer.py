"""Compile arrangements into deterministic in-memory timed commands."""

from arrangement import build
from timing import to_microseconds, validate


def _sort_key(command):
    return (
        command['tick'],
        0 if command['kind'] == 'off' else 1,
        command['pitch'],
        command['track'],
        command['cycle'],
        command['id'],
    )


def compile(tracks, tempo, ticks_per_beat=480):
    """Compile tracks into detached note-on/note-off commands."""
    validate(tempo, ticks_per_beat)
    arranged = build(tracks)
    notes = arranged['notes']

    microseconds_at = {}

    def microseconds(tick):
        if tick not in microseconds_at:
            microseconds_at[tick] = to_microseconds(
                tick, tempo, ticks_per_beat)
        return microseconds_at[tick]

    commands = []
    for note in notes:
        end = note['start'] + note['duration']
        common = {
            'pitch': note['pitch'],
            'track': note['track'],
            'cycle': note['cycle'],
            'id': note['id'],
        }
        commands.append({
            'tick': note['start'],
            'microseconds': microseconds(note['start']),
            'kind': 'on',
            'pitch': common['pitch'],
            'velocity': note['velocity'],
            'track': common['track'],
            'cycle': common['cycle'],
            'id': common['id'],
        })
        commands.append({
            'tick': end,
            'microseconds': microseconds(end),
            'kind': 'off',
            'pitch': common['pitch'],
            'velocity': 0,
            'track': common['track'],
            'cycle': common['cycle'],
            'id': common['id'],
        })

    commands.sort(key=_sort_key)
    end_tick = max((note['start'] + note['duration'] for note in notes),
                   default=0)
    return {
        'commands': commands,
        'end_tick': end_tick,
        'end_microseconds': microseconds(end_tick),
        'notes': len(notes),
        'tracks': sorted(arranged['tracks']),
    }
