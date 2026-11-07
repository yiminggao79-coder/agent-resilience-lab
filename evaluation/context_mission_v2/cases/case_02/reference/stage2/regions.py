"""Same-color connected components and a pure flood-fill operation."""
from canvas import owned
from palette import color, integer

def _diagonal(value):
    if type(value) is not bool:
        raise ValueError('diagonal must be a boolean')
    return value

def _region(grid, start, diagonal):
    height, width = len(grid), len(grid[0])
    target = grid[start[0]][start[1]]
    seen, pending = {start}, [start]
    offsets = [(-1, 0), (0, -1), (0, 1), (1, 0)]
    if diagonal:
        offsets += [(-1, -1), (-1, 1), (1, -1), (1, 1)]
    while pending:
        row, column = pending.pop()
        for dr, dc in offsets:
            neighbor = row + dr, column + dc
            r, c = neighbor
            if 0 <= r < height and 0 <= c < width and neighbor not in seen and grid[r][c] == target:
                seen.add(neighbor)
                pending.append(neighbor)
    return sorted(seen)

def _bounds(points):
    return [min(r for r, c in points), min(c for r, c in points),
            max(r for r, c in points) + 1, max(c for r, c in points) + 1]

def components(grid, diagonal=False):
    grid = owned(grid)
    _diagonal(diagonal)
    seen, result = set(), []
    for row in range(len(grid)):
        for column in range(len(grid[0])):
            if (row, column) not in seen:
                points = _region(grid, (row, column), diagonal)
                seen.update(points)
                result.append({'color': grid[row][column], 'size': len(points),
                               'anchor': [row, column], 'bounds': _bounds(points)})
    return result

def paint(grid, start, replacement, diagonal=False):
    grid = owned(grid)
    _diagonal(diagonal)
    color(replacement)
    if not isinstance(start, list) or len(start) != 2:
        raise ValueError('start must be [row, column]')
    row, column = integer(start[0], 0), integer(start[1], 0)
    if row >= len(grid) or column >= len(grid[0]):
        raise ValueError('start is outside grid')
    changed = []
    if grid[row][column] != replacement:
        changed = _region(grid, (row, column), diagonal)
        for r, c in changed:
            grid[r][c] = replacement
    return {'grid': grid, 'changed': [list(point) for point in changed],
            'bounds': _bounds(changed) if changed else None}
