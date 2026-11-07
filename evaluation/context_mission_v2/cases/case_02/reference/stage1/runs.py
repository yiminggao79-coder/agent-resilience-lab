"""Row-major run-length packets."""
from canvas import owned
from palette import color, integer

def pack(grid):
    grid = owned(grid)
    width, height = (len(grid[0]) if grid else 0), len(grid)
    runs = []
    previous, count = None, 0
    for row in grid:
        for pixel in row:
            if pixel == previous:
                count += 1
            else:
                if count:
                    runs.append([count, previous])
                previous, count = pixel, 1
    if count:
        runs.append([count, previous])
    return {'width': width, 'height': height, 'runs': runs}

def unpack(packet):
    if not isinstance(packet, dict):
        raise ValueError('packet must be a dictionary')
    try:
        width, height = integer(packet['width'], 0), integer(packet['height'], 0)
        runs = packet['runs']
    except KeyError:
        raise ValueError('packet fields missing') from None
    if (width == 0) != (height == 0) or not isinstance(runs, list):
        raise ValueError('invalid dimensions or runs')
    flat = []
    for run in runs:
        if not isinstance(run, list) or len(run) != 2:
            raise ValueError('run must be [count, color]')
        count, pixel = integer(run[0], 1), color(run[1])
        flat.extend([pixel] * count)
    if len(flat) != width * height:
        raise ValueError('run length does not match dimensions')
    return [flat[offset:offset + width] for offset in range(0, len(flat), width)] if width else []
