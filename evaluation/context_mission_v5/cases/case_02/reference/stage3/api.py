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
