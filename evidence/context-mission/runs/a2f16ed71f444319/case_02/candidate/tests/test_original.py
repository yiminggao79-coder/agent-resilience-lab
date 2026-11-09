import copy
import unittest
from canvas import owned
from runs import pack, unpack
from metrics import histogram

class OriginalContracts(unittest.TestCase):
    def test_final_run_is_present(self):
        self.assertEqual(pack([[1, 1, 2], [2, 2, 2]]),
            {'width': 3, 'height': 2, 'runs': [[2, 1], [4, 2]]})
    def test_single_pixel(self):
        self.assertEqual(unpack(pack([[9]])), [[9]])
    def test_empty_roundtrip(self):
        self.assertEqual(pack([]), {'width': 0, 'height': 0, 'runs': []})
        self.assertEqual(unpack(pack([])), [])
    def test_exact_packet_area(self):
        for runs in [[[1, 3]], [[3, 3]]]:
            with self.assertRaises(ValueError):
                unpack({'width': 2, 'height': 1, 'runs': runs})
    def test_noncanonical_runs_accepted(self):
        self.assertEqual(unpack({'width': 2, 'height': 1, 'runs': [[1, 4], [1, 4]]}), [[4, 4]])
    def test_histogram(self):
        self.assertEqual(histogram([[0, 1], [1, 9]]), {'0': 1, '1': 2, '9': 1})
    def test_copy(self):
        grid = [[1, 2], [3, 4]]
        before = copy.deepcopy(grid)
        owned(grid)[0][0] = 9
        self.assertEqual(grid, before)
    def test_invalid_grid(self):
        for grid in [[[1], [1, 2]], [[]], [[True]], [[10]]]:
            with self.assertRaises(ValueError):
                owned(grid)
    def test_invalid_run(self):
        for run in [[0, 1], [True, 1], [1, False], [1, 10]]:
            with self.assertRaises(ValueError):
                unpack({'width': 1, 'height': 1, 'runs': [run]})
    def test_dimension_pair(self):
        with self.assertRaises(ValueError):
            unpack({'width': 0, 'height': 1, 'runs': []})

if __name__ == '__main__':
    unittest.main()
