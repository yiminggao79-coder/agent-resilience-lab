"""Owned note events and simple polyphonic measures."""
from pitch import parse
def integer(value,minimum=0):
    if type(value) is not int or value<minimum: raise ValueError('invalid integer')
    return value
def identity(value):
    if not isinstance(value,str) or not value or value.strip()!=value: raise ValueError('invalid event identity')
    return value
def canonical(events):
    if not isinstance(events,list): raise ValueError('events must be a list')
    output=[]
    seen=set()
    for event in events:
        if not isinstance(event,dict): raise ValueError('event must be a dictionary')
        try:
            key=identity(event['id'])
            start=integer(event['start'])
            duration=integer(event['duration'],1)
            pitch=parse(event['pitch'])
            velocity=integer(event.get('velocity',64),1)
        except KeyError: raise ValueError('event field missing') from None
        if key in seen or velocity>127: raise ValueError('duplicate event or invalid velocity')
        seen.add(key)
        output.append(dict(id=key,start=start,duration=duration,pitch=pitch,velocity=velocity))
    return sorted(output,key=lambda event:event['id'])
def measure(notes):
    return {'notes':len(notes),'end':max((event['start']+event['duration'] for event in notes),default=0),
            'duration_sum':sum(event['duration'] for event in notes),'pitches':sorted({event['pitch'] for event in notes})}
