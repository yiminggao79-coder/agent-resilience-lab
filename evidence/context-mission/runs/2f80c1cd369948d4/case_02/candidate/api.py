"""JSON-ready musical event observations, without audio or external devices."""
from arrangement import build
from score import canonical,measure
from sequencer import compile as compile_tracks
from transforms import apply
def normalize(events):
    notes=canonical(events)
    return {'events':notes,'metrics':measure(notes)}
def transform_report(events,**options):
    notes=apply(events,**options)
    return {'events':notes,'metrics':measure(notes)}
def arrange(tracks): return build(tracks)
def render(tracks,tempo,ticks_per_beat=480):
    return compile_tracks(tracks,tempo,ticks_per_beat)
