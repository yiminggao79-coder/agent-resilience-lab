"""Track transformations, repetition and half-open clipping."""
from score import integer,identity,measure,by_track
from transforms import apply
def build(tracks):
    if not isinstance(tracks,list): raise ValueError('tracks must be a list')
    seen=set()
    output=[]
    for track in tracks:
        if not isinstance(track,dict): raise ValueError('invalid track')
        try:
            key=identity(track['id'])
            source=track['events']
        except KeyError: raise ValueError('track field missing') from None
        if key in seen: raise ValueError('duplicate track')
        seen.add(key)
        options=track.get('transform',{})
        if not isinstance(options,dict) or set(options)-{'transpose','stretch','shift','reverse'}: raise ValueError('unknown transform option')
        notes=apply(source,**options)
        repeat=integer(track.get('repeat',1),1)
        period=track.get('period')
        if period is None:
            period=measure(notes)['end']
            if repeat>1 and period==0: raise ValueError('repeated empty track needs a period')
        else: integer(period,1)
        crop=track.get('crop')
        if crop is not None:
            if not isinstance(crop,list) or len(crop)!=2: raise ValueError('crop must be [start,end]')
            start,end=integer(crop[0]),integer(crop[1])
            if start>=end: raise ValueError('crop must have positive span')
        for cycle in range(repeat):
            for note in notes:
                start=note['start']+cycle*period
                end=start+note['duration']
                if crop is not None:
                    start=max(start,crop[0])
                    end=min(end,crop[1])
                if start<end: output.append(dict(note,track=key,cycle=cycle,start=start,duration=end-start))
    output.sort(key=lambda note:(note['start'],note['pitch'],note['track'],note['cycle'],note['id']))
    return {'notes':output,'metrics':measure(output),'tracks':by_track(output,seen)}
