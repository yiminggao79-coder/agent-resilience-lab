"""Stable release/attack commands from a composed arrangement."""
from arrangement import build
from timing import validate,to_microseconds
def compile(tracks,tempo,ticks_per_beat=480):
    tempo=validate(tempo,ticks_per_beat)
    score=build(tracks)
    commands=[]
    for note in score['notes']:
        for kind,tick,velocity in [('on',note['start'],note['velocity']),('off',note['start']+note['duration'],0)]:
            commands.append({'tick':tick,'microseconds':to_microseconds(tick,tempo,ticks_per_beat),
                'kind':kind,'pitch':note['pitch'],'velocity':velocity,'track':note['track'],'cycle':note['cycle'],'id':note['id']})
    commands.sort(key=lambda command:(command['tick'],0 if command['kind']=='off' else 1,
        command['pitch'],command['track'],command['cycle'],command['id']))
    end=score['metrics']['end']
    return {'commands':commands,'end_tick':end,'end_microseconds':to_microseconds(end,tempo,ticks_per_beat),
            'notes':len(score['notes']),'tracks':sorted(score['tracks'])}
