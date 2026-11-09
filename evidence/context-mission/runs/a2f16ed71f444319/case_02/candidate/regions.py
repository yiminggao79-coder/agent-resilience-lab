"""Connected same-color regions and single-region painting."""
from canvas import owned
from palette import color, integer


def _flag(value):
    """Return ``value`` only when it is an actual bool (not 0/1 etc.)."""
    if type(value) is not bool:
        raise ValueError('diagonal must be a boolean')
    return value


def _neighbors(rows, columns, row, column, diagonal):
    """Yield in-bounds neighbors of (row, column) in a fixed row-major-ish order."""
    offsets = [(-1, 0), (0, -1), (0, 1), (1, 0)]
    if diagonal:
        offsets.extend([(-1, -1), (-1, 1), (1, -1), (1, 1)])
    for delta_row, delta_column in offsets:
        next_row = row + delta_row
        next_column = column + delta_column
        if 0 <= next_row < rows and 0 <= next_column < columns:
            yield next_row, next_column


def _region(grid, row, column, diagonal):
    """Collect the maximal same-color component containing (row, column)."""
    rows, columns = len(grid), len(grid[0])
    target = grid[row][column]
    seen = {(row, column)}
    stack = [(row, column)]
    pixels = [(row, column)]
    while stack:
        current_row, current_column = stack.pop()
        for next_row, next_column in _neighbors(rows, columns, current_row,
                                                current_column, diagonal):
            if (next_row, next_column) in seen:
                continue
            if grid[next_row][next_column] != target:
                continue
            seen.add((next_row, next_column))
            stack.append((next_row, next_column))
            pixels.append((next_row, next_column))
    pixels.sort()
    return target, pixels


def _bounds(pixels):
    """Half-open bounding box of a nonempty list of (row, column) pixels."""
    rows = [pixel[0] for pixel in pixels]
    columns = [pixel[1] for pixel in pixels]
    return [min(rows), min(columns), max(rows) + 1, max(columns) + 1]


def components(grid, diagonal=False):
    """List maximal same-color regions, row-major by anchor coordinate."""
    diagonal = _flag(diagonal)
    grid = owned(grid)
    if not grid:
        return []
    rows, columns = len(grid), len(grid[0])
    assigned = set()
    found = []
    for row in range(rows):
        for column in range(columns):
            if (row, column) in assigned:
                continue
            target, pixels = _region(grid, row, column, diagonal)
            assigned.update(pixels)
            first_row, first_column = pixels[0]
            found.append({
                'color': target,
                'size': len(pixels),
                'anchor': [first_row, first_column],
                'bounds': _bounds(pixels),
            })
    found.sort(key=lambda item: (item['anchor'][0], item['anchor'][1]))
    return found


def paint(grid, start, replacement, diagonal=False):
    """Recolor the original component containing ``start`` with ``replacement``."""
    diagonal = _flag(diagonal)
    grid = owned(grid)
    replacement = color(replacement)
    if not isinstance(start, list) or len(start) != 2:
        raise ValueError('start must be a two-element list of indices')
    if not grid:
        raise ValueError('empty grid has no valid start')
    row = integer(start[0], 0)
    column = integer(start[1], 0)
    if row >= len(grid) or column >= len(grid[0]):
        raise ValueError('start is outside the grid')
    target, pixels = _region(grid, row, column, diagonal)
    output = [list(line) for line in grid]
    if replacement == target:
        return {'grid': output, 'changed': [], 'bounds': None}
    changed = []
    for changed_row, changed_column in pixels:
        output[changed_row][changed_column] = replacement
        changed.append([changed_row, changed_column])
    return {'grid': output, 'changed': changed, 'bounds': _bounds(pixels)}
