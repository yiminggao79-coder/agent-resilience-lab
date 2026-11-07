"""JSON histograms use string keys."""
from canvas import owned

def histogram(grid):
    counts = {}
    for row in owned(grid):
        for pixel in row:
            key = str(pixel)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))
