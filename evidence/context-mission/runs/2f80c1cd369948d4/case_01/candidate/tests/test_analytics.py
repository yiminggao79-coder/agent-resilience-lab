import copy
import unittest
from api import analyze
from analytics import aggregate
from ordering import sort_table


def T(columns, rows):
    return {'columns': columns, 'rows': rows}


class AggregateTests(unittest.TestCase):
    def test_named_operations_and_first_seen_order(self):
        table = T(['g', 'v'], [['b', None], ['a', 2], ['b', 4], ['a', None], ['b', 6]])
        measures = [
            {'as': 'n', 'op': 'count_all'},
            {'as': 'present', 'op': 'count', 'column': 'v'},
            {'as': 'uniq', 'op': 'distinct', 'column': 'v'},
            {'as': 'total', 'op': 'sum', 'column': 'v'},
            {'as': 'lo', 'op': 'min', 'column': 'v'},
            {'as': 'hi', 'op': 'max', 'column': 'v'},
        ]
        self.assertEqual(aggregate(table, ['g'], measures), {
            'columns': ['g', 'n', 'present', 'uniq', 'total', 'lo', 'hi'],
            'rows': [['b', 3, 2, 2, 10, 4, 6], ['a', 2, 1, 1, 2, 2, 2]],
        })

    def test_all_null_values(self):
        table = T(['g', 'v'], [['a', None], ['b', None]])
        got = aggregate(table, ['g'], [
            {'as': 'c', 'op': 'count', 'column': 'v'},
            {'as': 'd', 'op': 'distinct', 'column': 'v'},
            {'as': 's', 'op': 'sum', 'column': 'v'},
            {'as': 'lo', 'op': 'min', 'column': 'v'},
            {'as': 'hi', 'op': 'max', 'column': 'v'},
        ])
        self.assertEqual(got['rows'], [['a', 0, 0, 0, None, None], ['b', 0, 0, 0, None, None]])

    def test_empty_keys_and_empty_table(self):
        got = aggregate(T(['v'], [[1], [2], [1]]), [], [{'as': 's', 'op': 'sum', 'column': 'v'}])
        self.assertEqual(got, {'columns': ['s'], 'rows': [[4]]})
        got = aggregate(T(['g', 'v'], []), ['g'], [{'as': 'n', 'op': 'count_all'}])
        self.assertEqual(got, {'columns': ['g', 'n'], 'rows': []})
        got = aggregate(T(['g', 'v'], []), [], [{'as': 'n', 'op': 'count_all'}])
        self.assertEqual(got, {'columns': ['n'], 'rows': []})

    def test_null_group_keys_and_text_min_max(self):
        table = T(['g', 'v'], [[None, 'z'], [None, 'b'], ['a', None], ['a', 'm']])
        got = aggregate(table, ['g'], [
            {'as': 'lo', 'op': 'min', 'column': 'v'},
            {'as': 'hi', 'op': 'max', 'column': 'v'},
        ])
        self.assertEqual(got['rows'], [[None, 'b', 'z'], ['a', 'm', 'm']])

    def test_sum_rejects_text_and_min_max_reject_mixed_types(self):
        with self.assertRaises(ValueError):
            aggregate(T(['g', 'v'], [['a', 'x']]), ['g'], [{'as': 's', 'op': 'sum', 'column': 'v'}])
        with self.assertRaises(ValueError):
            aggregate(T(['g', 'v'], [['a', 1], ['a', 'x']]), ['g'], [{'as': 'lo', 'op': 'min', 'column': 'v'}])

    def test_measure_validation_even_on_empty_table(self):
        table = T(['g', 'v'], [])
        bad_measures = [
            [],
            {'as': 'n', 'op': 'count_all'},
            [{'op': 'count_all'}],
            [{'as': ' ', 'op': 'count_all'}],
            [{'as': 'n', 'op': 'count_all'}, {'as': 'n', 'op': 'count_all'}],
            [{'as': 'g', 'op': 'count_all'}],
            [{'as': 'n', 'op': 'unknown'}],
            [{'as': 'n', 'op': 'count_all', 'column': 'v'}],
            [{'as': 'n', 'op': 'count'}],
            [{'as': 'n', 'op': 'count', 'column': 'missing'}],
            [{'as': 'n', 'op': 'count', 'column': None}],
        ]
        for measures in bad_measures:
            with self.assertRaises(ValueError):
                aggregate(table, ['g'], measures)

    def test_bad_keys_and_detachment(self):
        with self.assertRaises(ValueError):
            aggregate(T(['g', 'v'], [[1, 2]]), ['missing'], [{'as': 'n', 'op': 'count_all'}])
        with self.assertRaises(ValueError):
            aggregate(T(['g', 'v'], [[1, 2]]), ['g', 'g'], [{'as': 'n', 'op': 'count_all'}])
        table = T(['g', 'v'], [['a', 1]])
        before = copy.deepcopy(table)
        got = aggregate(table, ['g'], [{'as': 'total', 'op': 'sum', 'column': 'v'}])
        self.assertEqual(table, before)
        got['columns'][0] = 'changed'
        got['rows'][0][0] = 'changed'
        self.assertEqual(table, before)


class SortTableTests(unittest.TestCase):
    def test_no_keys_preserves_order_and_is_detached(self):
        table = T(['x'], [[2], [1]])
        before = copy.deepcopy(table)
        got = sort_table(table)
        self.assertEqual(got, table)
        got['columns'][0] = 'y'
        got['rows'][0][0] = 99
        self.assertEqual(table, before)

    def test_null_placement_is_independent_of_direction(self):
        table = T(['x'], [[None], [2], [1], [None], [3]])
        cases = [
            ([{'column': 'x'}], [[1], [2], [3], [None], [None]]),
            ([{'column': 'x', 'nulls': 'first'}], [[None], [None], [1], [2], [3]]),
            ([{'column': 'x', 'descending': True}], [[3], [2], [1], [None], [None]]),
            ([{'column': 'x', 'descending': True, 'nulls': 'first'}], [[None], [None], [3], [2], [1]]),
        ]
        for keys, expected in cases:
            self.assertEqual(sort_table(table, keys)['rows'], expected)

    def test_lexicographic_repeated_values_and_stable_ties(self):
        table = T(['a', 'b', 'id'], [[1, 2, 'x'], [1, 1, 'y'], [2, 0, 'z'], [1, 1, 'w']])
        got = sort_table(table, [
            {'column': 'a'},
            {'column': 'b', 'descending': True},
        ])
        self.assertEqual(got['rows'], [[1, 2, 'x'], [1, 1, 'y'], [1, 1, 'w'], [2, 0, 'z']])

    def test_null_ties_fall_through_to_later_keys(self):
        table = T(['a', 'b'], [[None, 1], [None, 2], [1, 0]])
        got = sort_table(table, [
            {'column': 'a', 'nulls': 'first'},
            {'column': 'b', 'descending': True},
        ])
        self.assertEqual(got['rows'], [[None, 2], [None, 1], [1, 0]])

    def test_mixed_types_are_rejected_in_used_column(self):
        with self.assertRaises(ValueError):
            sort_table(T(['x', 'y'], [[1, 0], ['a', 1]]), [{'column': 'x'}])
        self.assertEqual(sort_table(T(['x', 'y'], [[1, 0], ['a', 1]]), [])['rows'], [[1, 0], ['a', 1]])

    def test_sort_key_validation_even_on_empty_table(self):
        table = T(['x', 'y'], [])
        bad_keys = [
            'x',
            [{}],
            ['x'],
            [{'column': 'missing'}],
            [{'column': 'x', 'descending': 1}],
            [{'column': 'x', 'descending': 0}],
            [{'column': 'x', 'descending': None}],
            [{'column': 'x', 'nulls': 'middle'}],
            [{'column': 'x'}, {'column': 'x'}],
        ]
        for keys in bad_keys:
            with self.assertRaises(ValueError):
                sort_table(table, keys)

    def test_text_sort_and_input_preservation(self):
        table = T(['x'], [['b'], ['a'], ['c']])
        before = copy.deepcopy(table)
        got = sort_table(table, [{'column': 'x', 'descending': True}])
        self.assertEqual(got['rows'], [['c'], ['b'], ['a']])
        self.assertEqual(table, before)


class AnalyzeTests(unittest.TestCase):
    def test_aggregate_then_sort_and_default_order(self):
        table = T(['g', 'v'], [['b', 1], ['a', 2], ['b', 3]])
        measures = [
            {'as': 'n', 'op': 'count_all'},
            {'as': 'total', 'op': 'sum', 'column': 'v'},
        ]
        self.assertEqual(analyze(table, ['g'], measures)['rows'], [['b', 2, 4], ['a', 1, 2]])
        self.assertEqual(
            analyze(table, ['g'], measures, [{'column': 'g'}])['rows'],
            [['a', 1, 2], ['b', 2, 4]],
        )


if __name__ == '__main__':
    unittest.main()
