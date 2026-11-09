import copy
import unittest
from rows import owned
from joins import left_join
from grouping import summarize
from api import combine

def T(columns, rows):
    return {'columns': columns, 'rows': rows}

class LeftJoinRepairs(unittest.TestCase):
    def test_all_duplicate_matches_in_right_order(self):
        got = left_join(T(['k'], [[1]]), T(['k', 'v'], [[1, 2], [2, 8], [1, 3], [1, 4]]), ['k'], ['k'])
        self.assertEqual(got['columns'], ['k', 'r_k', 'r_v'])
        self.assertEqual(got['rows'], [[1, 1, 2], [1, 1, 3], [1, 1, 4]])

    def test_composite_null_never_matches_any_null(self):
        left = T(['a', 'b'], [[None, 1], [1, None], [None, None], [1, 2]])
        right = T(['a', 'b', 'v'], [[None, 1, 10], [1, None, 20], [None, None, 30], [1, 2, 40]])
        got = left_join(left, right, ['a', 'b'], ['a', 'b'])
        self.assertEqual(got['rows'], [
            [None, 1, None, None, None],
            [1, None, None, None, None],
            [None, None, None, None, None],
            [1, 2, 1, 2, 40],
        ])

    def test_left_order_and_unmatched_padding(self):
        got = left_join(T(['k'], [[2], [1], [3]]), T(['x', 'k'], [[7, 1], [8, 1]]), ['k'], ['k'])
        self.assertEqual(got['rows'], [[2, None, None], [1, 7, 1], [1, 8, 1], [3, None, None]])

    def test_inputs_unchanged_and_result_detached(self):
        left = T(['k'], [[1]])
        right = T(['k'], [[1]])
        before = copy.deepcopy((left, right))
        got = left_join(left, right, ['k'], ['k'])
        self.assertEqual((left, right), before)
        got['rows'][0][0] = 99
        got['columns'][0] = 'z'
        self.assertEqual((left, right), before)

    def test_reject_collisions_and_bad_keys(self):
        with self.assertRaises(ValueError):
            left_join(T(['k'], []), T(['k'], []), ['k'], ['k'], '')
        with self.assertRaises(ValueError):
            left_join(T(['k', 'r_k'], []), T(['k'], []), ['k'], ['k'], 'r_')
        for lk, rk in [([], []), (['k'], []), ([], ['k']), (['k', 'k'], ['k', 'k']),
                       (['missing'], ['k']), (['k'], ['missing']), ('k', ['k'])]:
            with self.assertRaises(ValueError):
                left_join(T(['k'], []), T(['k'], []), lk, rk)

class SummarizeRepairs(unittest.TestCase):
    def test_count_present_total(self):
        got = summarize(T(['g', 'v'], [['a', None], ['a', 4], ['a', None], ['a', -2]]), ['g'], 'v')
        self.assertEqual(got['rows'], [['a', 4, 2, 2]])

    def test_all_null_total_is_zero(self):
        self.assertEqual(summarize(T(['g', 'v'], [['a', None]]), ['g'], 'v')['rows'], [['a', 1, 0, 0]])

    def test_first_seen_and_null_group_keys(self):
        got = summarize(T(['g', 'v'], [[None, 2], ['a', 3], [None, 4]]), ['g'], 'v')
        self.assertEqual(got['rows'], [[None, 2, 2, 6], ['a', 1, 1, 3]])

    def test_composite_group_order(self):
        got = summarize(T(['a', 'b', 'v'], [[1, 2, 1], [1, 1, 2], [1, 2, 3]]), ['a', 'b'], 'v')
        self.assertEqual(got['rows'], [[1, 2, 2, 2, 4], [1, 1, 1, 1, 2]])

    def test_empty_keys_single_group_unless_no_rows(self):
        self.assertEqual(summarize(T(['v'], [[1], [None], [3]]), [], 'v')['rows'], [[3, 2, 4]])
        self.assertEqual(summarize(T(['v'], []), [], 'v')['rows'], [])

    def test_reject_nonintegers_booleans_and_reserved_names(self):
        with self.assertRaises(ValueError):
            summarize(T(['g', 'v'], [['a', 'x']]), ['g'], 'v')
        for bad in [True, False, 1.5]:
            with self.assertRaises(ValueError):
                summarize(T(['g', 'v'], [['a', bad]]), ['g'], 'v')
        for key in ['count', 'present', 'total']:
            with self.assertRaises(ValueError):
                summarize(T([key, 'v'], [['a', 1]]), [key], 'v')

    def test_reject_malformed_tables(self):
        for bad in [None, [], {'columns': ['x']}, {'columns': [], 'rows': []},
                    {'columns': ['x'], 'rows': [[1, 2]]},
                    {'columns': ['x', 'x'], 'rows': [[1, 2]]}]:
            with self.assertRaises(ValueError):
                summarize(bad, ['x'], 'x')

    def test_inputs_unchanged_and_result_detached(self):
        table = T(['g', 'v'], [['a', 1]])
        before = copy.deepcopy(table)
        got = summarize(table, ['g'], 'v')
        self.assertEqual(table, before)
        got['rows'][0][0] = 'z'
        got['rows'][0].append(9)
        got['columns'][0] = 'q'
        self.assertEqual(table, before)

    def test_combine_uses_corrected_interfaces(self):
        left = T(['k'], [[1], [2]])
        right = T(['k', 'v'], [[1, 4], [1, None], [2, 5]])
        before = copy.deepcopy((left, right))
        got = combine(left, right, ['k'], ['k'], ['r_k'], 'r_v')
        self.assertEqual(got['joined']['rows'], [[1, 1, 4], [1, 1, None], [2, 2, 5]])
        self.assertEqual(got['groups']['rows'], [[1, 2, 1, 4], [2, 1, 1, 5]])
        self.assertEqual((left, right), before)

if __name__ == '__main__':
    unittest.main()
