"""Regression tests for runs.pack / runs.unpack repairs."""
import copy
import unittest
from runs import pack, unpack


class PackRegression(unittest.TestCase):
    def test_merge_across_row_boundary(self):
        # Row boundary at index 3 joins two 0-valued pixels.
        self.assertEqual(pack([[1, 1, 0], [0, 2, 2]]),
                         {'width': 3, 'height': 2, 'runs': [[2, 1], [2, 0], [2, 2]]})

    def test_final_run_flushed(self):
        packet = pack([[5, 5, 5]])
        self.assertEqual(packet['runs'], [[3, 5]])

    def test_final_run_flushed_after_boundary_merge(self):
        packet = pack([[0, 0], [0, 0]])
        self.assertEqual(packet, {'width': 2, 'height': 2, 'runs': [[4, 0]]})

    def test_single_row_shapes(self):
        self.assertEqual(pack([[7]]), {'width': 1, 'height': 1, 'runs': [[1, 7]]})
        self.assertEqual(pack([[1, 2, 3]]), {'width': 3, 'height': 1, 'runs': [[1, 1], [1, 2], [1, 3]]})

    def test_column_shape(self):
        self.assertEqual(pack([[3], [4], [4]]),
                         {'width': 1, 'height': 3, 'runs': [[1, 3], [2, 4]]})

    def test_empty(self):
        self.assertEqual(pack([]), {'width': 0, 'height': 0, 'runs': []})

    def test_pack_preserves_input(self):
        grid = [[1, 1, 2], [2, 3, 3]]
        before = copy.deepcopy(grid)
        packet = pack(grid)
        self.assertEqual(grid, before)
        packet['runs'][0][1] = 9
        self.assertEqual(grid, before)

    def test_pack_rejects_invalid_grids(self):
        for grid in [[[1], [1, 2]], [[]], [[True]], [[10]], [[-1]], [('a',)], [[1.0]], 'ab', []]:
            if grid == []:
                continue
            with self.assertRaises(ValueError):
                pack(grid)
        for bad in [None, 'ab', 0]:
            with self.assertRaises(ValueError):
                pack(bad)


class UnpackRegression(unittest.TestCase):
    def test_roundtrip_shapes(self):
        for grid in ([[9]], [[1, 1, 2], [2, 2, 2]], [[0, 1], [1, 1]],
                     [[5, 5], [6, 7]], [[0, 0, 0], [1, 0, 0]]):
            self.assertEqual(unpack(pack(grid)), grid)

    def test_total_length_exact(self):
        # one too few
        with self.assertRaises(ValueError):
            unpack({'width': 2, 'height': 1, 'runs': [[1, 3]]})
        # one too many
        with self.assertRaises(ValueError):
            unpack({'width': 2, 'height': 1, 'runs': [[3, 3]]})
        # sum short but each run valid
        with self.assertRaises(ValueError):
            unpack({'width': 2, 'height': 2, 'runs': [[2, 1], [1, 2]]})
        # sum long
        with self.assertRaises(ValueError):
            unpack({'width': 1, 'height': 2, 'runs': [[2, 1], [1, 2]]})

    def test_empty_packet_exact(self):
        self.assertEqual(unpack({'width': 0, 'height': 0, 'runs': []}), [])
        with self.assertRaises(ValueError):
            unpack({'width': 0, 'height': 0, 'runs': [[1, 1]]})
        with self.assertRaises(ValueError):
            unpack({'width': 1, 'height': 1, 'runs': []})

    def test_repeated_adjacent_colors(self):
        self.assertEqual(unpack({'width': 2, 'height': 1, 'runs': [[1, 4], [1, 4]]}), [[4, 4]])
        self.assertEqual(unpack({'width': 4, 'height': 1, 'runs': [[2, 4], [2, 4]]}), [[4, 4, 4, 4]])

    def test_shape_is_rectangular(self):
        result = unpack({'width': 3, 'height': 2, 'runs': [[6, 1]]})
        self.assertEqual(result, [[1, 1, 1], [1, 1, 1]])
        for row in result:
            self.assertIsInstance(row, list)

    def test_fresh_output(self):
        runs = [[2, 1], [2, 2]]
        packet = {'width': 2, 'height': 2, 'runs': runs}
        before = copy.deepcopy(packet)
        grid = unpack(packet)
        self.assertEqual(packet, before)
        grid[0][0] = 9
        grid.append([0])
        self.assertEqual(packet, before)
        self.assertIsNot(grid[0], grid[1])

    def test_bad_dimensions(self):
        for packet in [
            {'width': 0, 'height': 1, 'runs': []},
            {'width': 1, 'height': 0, 'runs': []},
            {'width': -1, 'height': 1, 'runs': [[1, 1]]},
            {'width': 1, 'height': -1, 'runs': [[1, 1]]},
            {'width': True, 'height': 1, 'runs': [[1, 1]]},
            {'width': 1, 'height': False, 'runs': [[1, 1]]},
            {'width': 1.0, 'height': 1, 'runs': [[1, 1]]},
            {'width': 1, 'height': 1, 'runs': 'x'},
            {'width': 1, 'height': 1, 'runs': None},
        ]:
            with self.assertRaises(ValueError):
                unpack(packet)

    def test_bad_runs(self):
        for runs in [[[0, 1]], [[True, 1]], [[1, False]], [[1, 10]], [[-2, 1]],
                     [[1, -1]], [[1.0, 1]], [[1]], [[1, 2, 3]], [1], [(1, 2)]]:
            with self.assertRaises(ValueError):
                unpack({'width': 1, 'height': 1, 'runs': runs})

    def test_missing_fields(self):
        with self.assertRaises(ValueError):
            unpack({'width': 1, 'height': 1})
        with self.assertRaises(ValueError):
            unpack({'height': 1, 'runs': [[1, 1]]})
        with self.assertRaises(ValueError):
            unpack([1, 2, 3])


if __name__ == '__main__':
    unittest.main()
