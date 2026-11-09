import copy
import unittest

from api import enrich
from ordering import permutation, sort_table
from windows import compute


def T(columns, rows):
    return {'columns': columns, 'rows': rows}


class PermutationTests(unittest.TestCase):
    def test_matches_stable_sort_and_preserves_input(self):
        table = T(['g', 'v'], [['b', 2], ['a', None], ['b', 1], ['a', 1], ['b', 1]])
        before = copy.deepcopy(table)
        order = [{'column': 'v', 'nulls': 'last'}]
        indices = permutation(table, order)
        self.assertEqual(indices, [2, 3, 4, 0, 1])
        self.assertEqual([table['rows'][i] for i in indices], sort_table(table, order)['rows'])
        self.assertEqual(permutation(table), list(range(5)))
        self.assertEqual(table, before)


class WindowTests(unittest.TestCase):
    def test_ties_interleaved_partitions_nulls_and_lag_boundaries(self):
        table = T(['g', 'v', 'id'], [
            ['a', 5, 0],
            ['b', 2, 1],
            ['a', 1, 2],
            [None, 4, 3],
            ['a', 2, 4],
            ['b', 1, 5],
            [None, None, 6],
            ['b', 2, 7],
            ['a', None, 8],
        ])
        before = copy.deepcopy(table)
        calculations = [
            {'as': 'rn', 'op': 'row_number'},
            {'as': 'rk', 'op': 'rank'},
            {'as': 'dr', 'op': 'dense_rank'},
            {'as': 'run', 'op': 'running_sum', 'column': 'v'},
            {'as': 'prev', 'op': 'lag', 'column': 'v'},
            {'as': 'far', 'op': 'lag', 'column': 'v', 'offset': 3, 'default': 'x'},
        ]
        got = compute(table, ['g'], [{'column': 'v', 'nulls': 'last'}], calculations)
        self.assertEqual(got, {
            'columns': ['g', 'v', 'id', 'rn', 'rk', 'dr', 'run', 'prev', 'far'],
            'rows': [
                ['a', 5, 0, 3, 3, 3, 8, 2, 'x'],
                ['b', 2, 1, 2, 2, 2, 3, 1, 'x'],
                ['a', 1, 2, 1, 1, 1, 1, None, 'x'],
                [None, 4, 3, 1, 1, 1, 4, None, 'x'],
                ['a', 2, 4, 2, 2, 2, 3, 1, 'x'],
                ['b', 1, 5, 1, 1, 1, 1, None, 'x'],
                [None, None, 6, 2, 2, 2, 4, 4, 'x'],
                ['b', 2, 7, 3, 2, 2, 5, 2, 'x'],
                ['a', None, 8, 4, 4, 4, 8, 5, 1],
            ],
        })
        self.assertEqual(table, before)

    def test_no_order_keys_make_all_rows_peers(self):
        table = T(['g', 'v'], [['a', 3], ['b', 5], ['a', None], ['b', 7]])
        got = compute(table, ['g'], [], [
            {'as': 'rn', 'op': 'row_number'},
            {'as': 'rk', 'op': 'rank'},
            {'as': 'dr', 'op': 'dense_rank'},
            {'as': 'run', 'op': 'running_sum', 'column': 'v'},
        ])
        self.assertEqual(got['rows'], [
            ['a', 3, 1, 1, 1, 3],
            ['b', 5, 1, 1, 1, 5],
            ['a', None, 2, 1, 1, 3],
            ['b', 7, 2, 1, 1, 12],
        ])

    def test_lag_defaults_and_returned_table_isolation(self):
        table = T(['g', 'v'], [['a', 10], ['a', None], ['a', 20]])
        before = copy.deepcopy(table)
        calculations = [
            {'as': 'prev', 'op': 'lag', 'column': 'v'},
            {'as': 'far', 'op': 'lag', 'column': 'v', 'offset': 2, 'default': -5},
        ]
        got = compute(table, ['g'], [], calculations)
        self.assertEqual(got['rows'], [
            ['a', 10, None, -5],
            ['a', None, 10, -5],
            ['a', 20, None, 10],
        ])
        got['columns'].append('extra')
        got['rows'][0][0] = 'changed'
        got['rows'][1].append('changed')
        self.assertEqual(table, before)
        self.assertEqual(compute(table, ['g'], [], calculations)['rows'], [
            ['a', 10, None, -5],
            ['a', None, 10, -5],
            ['a', 20, None, 10],
        ])

    def test_validation_on_empty_tables(self):
        empty = T(['g', 'v'], [])
        bad_calculations = [
            [],
            [{'as': 'g', 'op': 'row_number'}],
            [{'as': 'x', 'op': 'unknown'}],
            [{'as': 'x', 'op': 'row_number', 'column': 'v'}],
            [{'as': 'x', 'op': 'running_sum'}],
            [{'as': 'x', 'op': 'lag', 'column': 'v', 'offset': True}],
            [{'as': 'x', 'op': 'lag', 'column': 'v', 'offset': 0}],
            [{'as': 'x', 'op': 'lag', 'column': 'v', 'default': False}],
            [{'as': 'x', 'op': 'row_number'}, {'as': 'x', 'op': 'rank'}],
        ]
        for calculations in bad_calculations:
            with self.subTest(calculations=calculations):
                with self.assertRaises(ValueError):
                    compute(empty, ['g'], [], calculations)
        with self.assertRaises(ValueError):
            compute(empty, ['missing'], [], [{'as': 'x', 'op': 'row_number'}])
        with self.assertRaises(ValueError):
            compute(empty, ['g'], [{'column': 'missing'}], [{'as': 'x', 'op': 'row_number'}])

    def test_running_sum_rejects_present_text(self):
        table = T(['v'], [[1], ['x'], [2]])
        with self.assertRaises(ValueError):
            compute(table, [], [], [{'as': 's', 'op': 'running_sum', 'column': 'v'}])


class EnrichTests(unittest.TestCase):
    def test_computed_and_ordered_tables_are_independent(self):
        table = T(['g', 'v'], [['a', 2], ['b', 1], ['a', 3]])
        before = copy.deepcopy(table)
        result = enrich(
            table,
            ['g'],
            [{'column': 'v'}],
            [{'as': 'rn', 'op': 'row_number'}],
            [{'column': 'rn', 'descending': True}],
        )
        self.assertEqual(result['table'], {
            'columns': ['g', 'v', 'rn'],
            'rows': [['a', 2, 1], ['b', 1, 1], ['a', 3, 2]],
        })
        self.assertEqual(result['ordered']['rows'], [['a', 3, 2], ['a', 2, 1], ['b', 1, 1]])
        self.assertEqual(table, before)

        computed = result['table']
        ordered = result['ordered']
        computed['columns'].append('extra')
        computed['rows'][0][0] = 'changed'
        self.assertEqual(ordered['rows'], [['a', 3, 2], ['a', 2, 1], ['b', 1, 1]])
        ordered['rows'][0][0] = 'also changed'
        self.assertEqual(computed['rows'][0], ['changed', 2, 1])
        self.assertEqual(table, before)


if __name__ == '__main__':
    unittest.main()
