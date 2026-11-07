"""Rectangular grids are copied before returning them."""
from palette import color

def owned(grid):
    if not isinstance(grid, list):
        raise ValueError('grid must be a list')
    if not grid:
        return []
    if not isinstance(grid[0], list) or not grid[0]:
        raise ValueError('nonempty grid rows must be nonempty lists')
    width = len(grid[0])
    output = []
    for row in grid:
        if not isinstance(row, list) or len(row) != width:
            raise ValueError('grid must be rectangular')
        output.append([color(pixel) for pixel in row])
    return output
