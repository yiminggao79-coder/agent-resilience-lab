"""JSON-ready musical event observations, without audio or external devices."""
from score import canonical,measure
def normalize(events):
    notes=canonical(events)
    return {'events':notes,'metrics':measure(notes)}
