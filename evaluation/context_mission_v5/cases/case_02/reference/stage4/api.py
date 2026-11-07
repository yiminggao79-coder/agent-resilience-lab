"""JSON-ready musical event observations, without audio or external devices."""
from score import canonical,measure
def normalize(events):
    notes=canonical(events)
    return {'events':notes,'metrics':measure(notes)}

def transform_report(events,**options):
    from transforms import apply
    notes=apply(events,**options)
    return {'events':notes,'metrics':measure(notes)}

def arrange(tracks):
    from arrangement import build
    return build(tracks)

def render(tracks,tempo,ticks_per_beat=480):
    from sequencer import compile
    return compile(tracks,tempo,ticks_per_beat)
