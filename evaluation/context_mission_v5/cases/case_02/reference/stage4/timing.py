"""Piecewise tempo conversion, flooring only the accumulated total."""
from score import integer
def validate(tempo,ticks_per_beat):
    integer(ticks_per_beat,1)
    if not isinstance(tempo,list) or not tempo: raise ValueError('tempo must be nonempty')
    output=[]
    previous=-1
    for entry in tempo:
        if not isinstance(entry,dict): raise ValueError('invalid tempo entry')
        try: tick,rate=integer(entry['tick']),integer(entry['microseconds_per_beat'],1)
        except KeyError: raise ValueError('tempo field missing') from None
        if tick<=previous: raise ValueError('tempo ticks must strictly increase')
        previous=tick
        output.append(dict(tick=tick,microseconds_per_beat=rate))
    if output[0]['tick']!=0: raise ValueError('tempo must start at zero')
    return output
def to_microseconds(tick,tempo,ticks_per_beat=480):
    integer(tick)
    tempo=validate(tempo,ticks_per_beat)
    cursor=0
    rate=tempo[0]['microseconds_per_beat']
    numerator=0
    for change in tempo[1:]:
        if change['tick']>=tick: break
        numerator+=(change['tick']-cursor)*rate
        cursor=change['tick']
        rate=change['microseconds_per_beat']
    numerator+=(tick-cursor)*rate
    return numerator//ticks_per_beat
