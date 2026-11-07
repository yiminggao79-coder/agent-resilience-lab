"""Transpose, reverse, exact stretch, then shift."""
from score import canonical,measure
from pitch import transpose as shifted_pitch
from rhythm import ratio,scale
def apply(events,transpose=0,stretch=None,shift=0,reverse=False):
    if type(transpose) is not int or type(shift) is not int or type(reverse) is not bool: raise ValueError('invalid transform option')
    ratio(stretch)
    notes=canonical(events)
    end=measure(notes)['end']
    output=[]
    for event in notes:
        start=end-event['start']-event['duration'] if reverse else event['start']
        transformed=scale(start,stretch)+shift
        if transformed<0: raise ValueError('negative transformed start')
        output.append(dict(event,start=transformed,duration=scale(event['duration'],stretch),pitch=shifted_pitch(event['pitch'],transpose)))
    return canonical(output)
