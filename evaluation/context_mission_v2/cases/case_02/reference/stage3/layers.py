"""Clipped integer-color layers, followed by canonical observations."""
from canvas import owned
from palette import integer, color
from regions import components
from runs import pack

def render(base, layers, diagonal=False):
    base = owned(base)
    if type(diagonal) is not bool:
        raise ValueError('diagonal must be a boolean')
    if not isinstance(layers, list):
        raise ValueError('layers must be a list')
    prepared = []
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError('layer must be a dictionary')
        try:
            pixels = owned(layer['pixels'])
            top, left = integer(layer['top']), integer(layer['left'])
        except KeyError:
            raise ValueError('layer is missing a required field') from None
        transparent = layer.get('transparent', 0)
        if transparent is not None:
            color(transparent)
        prepared.append((pixels, top, left, transparent))
    output = [row[:] for row in base]
    height, width = len(base), len(base[0]) if base else 0
    written = set()
    for pixels, top, left, transparent in prepared:
        for row, values in enumerate(pixels):
            for column, pixel in enumerate(values):
                r, c = top + row, left + column
                if 0 <= r < height and 0 <= c < width and pixel != transparent:
                    output[r][c] = pixel
                    written.add((r, c))
    changed = [[r, c] for r in range(height) for c in range(width) if output[r][c] != base[r][c]]
    return {'grid': output, 'written': [list(point) for point in sorted(written)], 'changed': changed,
            'components': components(output, diagonal), 'packed': pack(output)}
