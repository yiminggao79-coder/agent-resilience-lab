import copy
import unittest
from regions import components, paint


class ComponentTests(unittest.TestCase):
    def test_empty_grid(self):
        self.assertEqual(components([]), [])
        self.assertEqual(components([], True), [])

    def test_single_pixel(self):
        self.assertEqual(components([[7]]), [
            {'color': 7, 'size': 1, 'anchor': [0, 0], 'bounds': [0, 0, 1, 1]}])

    def test_uniform_grid_one_component(self):
        self.assertEqual(components([[3, 3], [3, 3]]), [
            {'color': 3, 'size': 4, 'anchor': [0, 0], 'bounds': [0, 0, 2, 2]}])

    def test_zero_color_included(self):
        self.assertEqual(components([[0, 1], [0, 1]]), [
            {'color': 0, 'size': 2, 'anchor': [0, 0], 'bounds': [0, 0, 2, 1]},
            {'color': 1, 'size': 2, 'anchor': [0, 1], 'bounds': [0, 1, 2, 2]},
        ])

    def test_anchor_order_not_first_visit(self):
        grid = [[1, 2, 1], [2, 2, 2]]
        anchors = [item['anchor'] for item in components(grid)]
        self.assertEqual(anchors, [[0, 0], [0, 1]])
        sizes = {tuple(item['anchor']): item['size'] for item in components(grid)}
        self.assertEqual(sizes[(0, 1)], 5)

    def test_bounds_tight_and_exclusive(self):
        grid = [[0, 9, 0],
                [9, 9, 9],
                [0, 9, 0]]
        (only,) = components(grid)
        self.assertEqual(only['size'], 1)
        self.assertEqual(only['bounds'], [0, 1, 1, 2])
        ones = [item for item in components(grid) if item['color'] == 0]
        self.assertEqual(len(ones), 4)
        self.assertEqual(ones[-1]['anchor'], [2, 2])

    def test_diagonal_connection(self):
        grid = [[1, 0],
                [0, 1]]
        self.assertEqual([item['size'] for item in components(grid)], [1, 1, 1, 1])
        diagonals = components(grid, True)
        ones = [item for item in diagonals if item['color'] == 1]
        self.assertEqual(len(ones), 1)
        self.assertEqual(ones[0]['size'], 2)
        self.assertEqual(ones[0]['anchor'], [0, 0])
        self.assertEqual(ones[0]['bounds'], [0, 0, 2, 2])

    def test_diagonal_barrier(self):
        grid = [[1, 9, 1],
                [9, 9, 9],
                [1, 9, 1]]
        fours = [item for item in components(grid, True) if item['color'] == 1]
        self.assertEqual(len(fours), 4)
        self.assertEqual(sum(item['size'] for item in fours), 4)

    def test_side_barrier_blocks_diagonals(self):
        grid = [[1, 2, 1]]
        self.assertEqual(len(components(grid)), 2)
        self.assertEqual(len(components(grid, True)), 2)

    def test_components_cover_grid_exactly(self):
        grid = [[0, 1, 0], [1, 0, 1], [0, 1, 0]]
        for diagonal in (False, True):
            total = sum(item['size'] for item in components(grid, diagonal))
            self.assertEqual(total, 9)
            for item in components(grid, diagonal):
                min_row, min_column, max_row, max_column = item['bounds']
                self.assertLessEqual(min_row, item['anchor'][0] + 1)
                self.assertLessEqual(min_row, max_row)
                self.assertLessEqual(min_column, max_column)
                self.assertLessEqual(max_row, 3)
                self.assertLessEqual(max_column, 3)

    def test_diagonal_must_be_bool(self):
        for diagonal in (1, 0, None, 'yes', [True], 1.0):
            with self.assertRaises(ValueError):
                components([[1]], diagonal)
            with self.assertRaises(ValueError):
                paint([[1]], [0, 0], 2, diagonal)

    def test_invalid_grids(self):
        for grid in ([[1], [1, 2]], [[]], [[True]], [[10]], [[-1]], 'abc', None):
            with self.assertRaises(ValueError):
                components(grid)
            with self.assertRaises(ValueError):
                paint(grid, [0, 0], 1)

    def test_input_preserved_components(self):
        grid = [[1, 1], [2, 2]]
        before = copy.deepcopy(grid)
        result = components(grid)
        result[0]['bounds'][0] = 99
        result[0]['anchor'][1] = 99
        self.assertEqual(grid, before)


class PaintTests(unittest.TestCase):
    def test_basic_fill(self):
        grid = [[1, 1, 2],
                [1, 2, 2]]
        out = paint(grid, [0, 0], 5)
        self.assertEqual(out['grid'], [[5, 5, 2], [5, 2, 2]])
        self.assertEqual(out['changed'], [[0, 0], [0, 1], [1, 0]])
        self.assertEqual(out['bounds'], [0, 0, 2, 2])

    def test_only_started_component_selected(self):
        grid = [[1, 2, 1]]
        out = paint(grid, [0, 2], 3)
        self.assertEqual(out['grid'], [[1, 2, 3]])
        self.assertEqual(out['changed'], [[0, 2]])
        self.assertEqual(out['bounds'], [0, 2, 1, 3])

    def test_barrier_confines_fill(self):
        grid = [[1, 1, 2, 1],
                [1, 2, 2, 1]]
        out = paint(grid, [1, 3], 4)
        self.assertEqual(out['grid'], [[1, 1, 2, 4], [1, 2, 2, 4]])
        self.assertEqual(out['bounds'], [0, 3, 2, 4])

    def test_diagonal_fill(self):
        grid = [[1, 0, 1],
                [0, 1, 0],
                [1, 0, 1]]
        out = paint(grid, [0, 0], 6, True)
        self.assertEqual(out['changed'], [[0, 0], [0, 2], [1, 1], [2, 0], [2, 2]])
        self.assertEqual(out['bounds'], [0, 0, 3, 3])
        self.assertEqual(out['grid'][1][1], 6)
        self.assertEqual(out['grid'][1][0], 0)

    def test_diagonal_false_is_separate(self):
        grid = [[1, 0],
                [0, 1]]
        out = paint(grid, [0, 0], 7)
        self.assertEqual(out['changed'], [[0, 0]])

    def test_noop_same_color(self):
        grid = [[1, 1], [1, 2]]
        before = copy.deepcopy(grid)
        out = paint(grid, [0, 0], 1, True)
        self.assertEqual(out['changed'], [])
        self.assertIsNone(out['bounds'])
        self.assertEqual(out['grid'], before)
        self.assertEqual(grid, before)

    def test_noop_returns_fresh_grid(self):
        grid = [[2, 2]]
        out = paint(grid, [0, 1], 2)
        out['grid'][0][0] = 9
        self.assertEqual(grid, [[2, 2]])

    def test_input_preserved_on_change(self):
        grid = [[1, 2], [2, 2]]
        before = copy.deepcopy(grid)
        out = paint(grid, [0, 0], 0)
        self.assertEqual(grid, before)
        out['grid'][1][1] = 8
        self.assertEqual(grid, before)

    def test_start_on_all_edges(self):
        grid = [[1, 2, 3]]
        for column in range(3):
            out = paint(grid, [0, column], 9)
            self.assertEqual(out['changed'], [[0, column]])
        column_grid = [[1], [2], [3]]
        for row in range(3):
            out = paint(column_grid, [row, 0], 9)
            self.assertEqual(out['changed'], [[row, 0]])

    def test_corner_start_last_row(self):
        grid = [[1, 2], [3, 3]]
        out = paint(grid, [1, 1], 0)
        self.assertEqual(out['changed'], [[1, 0], [1, 1]])
        self.assertEqual(out['bounds'], [1, 0, 2, 2])

    def test_bad_start(self):
        grid = [[1, 2], [3, 4]]
        for start in ([2, 0], [0, 2], [-1, 0], [0, -1], [True, 0], [0, False],
                      [0], [0, 0, 0], (0, 0), '00', None, [0.0, 0], [0, '1']):
            with self.assertRaises(ValueError):
                paint(grid, start, 5)

    def test_empty_grid_start_invalid(self):
        with self.assertRaises(ValueError):
            paint([], [0, 0], 1)

    def test_bad_replacement(self):
        for replacement in (True, False, 10, -1, 1.5, None, '1'):
            with self.assertRaises(ValueError):
                paint([[1]], [0, 0], replacement)


if __name__ == '__main__':
    unittest.main()
