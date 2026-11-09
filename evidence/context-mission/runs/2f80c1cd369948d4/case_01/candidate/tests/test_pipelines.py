import copy
import unittest

from api import pipeline
from grouping import summarize
from joins import left_join
from projection import filter_rows, project
from query import execute


def T(columns, rows):
    return {'columns': columns, 'rows': rows}


class JoinAndSummaryRepairTests(unittest.TestCase):
    def test_left_join_duplicates_nulls_and_inputs(self):
        left = T(['id', 'v'], [[1, 'a'], [None, 'b'], [2, 'c'], [1, 'd']])
        right = T(['id', 'w'], [[1, 'x'], [1, 'y'], [None, 'z'], [2, 'q']])
        before_left = copy.deepcopy(left)
        before_right = copy.deepcopy(right)
        got = left_join(left, right, ['id'], ['id'], 'r_')
        self.assertEqual(got, {
            'columns': ['id', 'v', 'r_id', 'r_w'],
            'rows': [
                [1, 'a', 1, 'x'],
                [1, 'a', 1, 'y'],
                [None, 'b', None, None],
                [2, 'c', 2, 'q'],
                [1, 'd', 1, 'x'],
                [1, 'd', 1, 'y'],
            ],
        })
        got['rows'][0][0] = 'changed'
        self.assertEqual(left, before_left)
        self.assertEqual(right, before_right)

    def test_summarize_order_nulls_and_validation(self):
        table = T(['g', 'v'], [[None, None], ['a', 2], [None, 3], ['a', None]])
        got = summarize(table, ['g'], 'v')
        self.assertEqual(got, {
            'columns': ['g', 'count', 'present', 'total'],
            'rows': [[None, 2, 1, 3], ['a', 2, 1, 2]],
        })
        with self.assertRaises(ValueError):
            summarize(T(['g', 'v'], [['a', True]]), ['g'], 'v')
        with self.assertRaises(ValueError):
            summarize(T(['g', 'v', 'count'], [['a', 1, 2]]), ['g'], 'v')


class ProjectFilterTests(unittest.TestCase):
    def test_project_order_and_detachment(self):
        table = T(['a', 'b', 'c'], [[1, 2, 3], [4, 5, 6]])
        before = copy.deepcopy(table)
        got = project(table, ['c', 'a'])
        self.assertEqual(got, {'columns': ['c', 'a'], 'rows': [[3, 1], [6, 4]]})
        got['rows'][0][0] = 99
        got['columns'].append('x')
        self.assertEqual(table, before)
        for columns in ([], ['a', 'a'], ['missing']):
            with self.assertRaises(ValueError):
                project(table, columns)

    def test_filter_conditions_nulls_and_validation(self):
        table = T(['x', 'g'], [[None, 'a'], [1, 'a'], [2, 'b'], ['z', 'b']])
        self.assertEqual(
            filter_rows(table, [
                {'column': 'g', 'op': 'eq', 'value': 'b'},
                {'column': 'x', 'op': 'ne', 'value': None},
            ])['rows'],
            [[2, 'b'], ['z', 'b']],
        )
        self.assertEqual(
            filter_rows(table, [{'column': 'x', 'op': 'eq', 'value': None}])['rows'],
            [[None, 'a']],
        )
        with self.assertRaises(ValueError):
            filter_rows(table, [{'column': 'x', 'op': 'not_null'},
                                {'column': 'x', 'op': 'ge', 'value': 2}])
        self.assertEqual(
            filter_rows(T(['x'], [[1], [2]]),
                        [{'column': 'x', 'op': 'ge', 'value': 2}])['rows'],
            [[2]],
        )
        self.assertEqual(filter_rows(table, [])['rows'], table['rows'])
        with self.assertRaises(ValueError):
            filter_rows(table, [
                {'column': 'x', 'op': 'eq', 'value': 7},
                {'column': 'x', 'op': 'lt', 'value': 'a'},
            ])
        for conditions in (
            [{'column': 'x', 'op': 'lt', 'value': None}],
            [{'column': 'x', 'op': 'is_null', 'value': None}],
            [{'column': 'x', 'op': 'bogus', 'value': 1}],
            [{'column': 'x', 'op': 'eq'}],
        ):
            with self.assertRaises(ValueError):
                filter_rows(T(['x'], []), conditions)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.people = T(
            ['id', 'dept', 'score'],
            [[1, 'b', 10], [2, 'a', None], [3, 'b', 4],
             [4, 'a', 2], [1, 'b', 6]],
        )
        self.lookup = T(['id', 'tag'], [[1, 'x'], [1, 'y'], [2, 'z'], [None, 'q']])
        self.steps = [
            {'out': 'j', 'op': 'join', 'left': 'people', 'right': 'lookup',
             'left_keys': ['id'], 'right_keys': ['id'], 'prefix': 'r_'},
            {'out': 'f', 'op': 'filter', 'source': 'j', 'conditions': [
                {'column': 'score', 'op': 'not_null'},
                {'column': 'score', 'op': 'ge', 'value': 3},
            ]},
            {'out': 'w', 'op': 'window', 'source': 'f', 'partition_by': ['dept'],
             'order_by': [{'column': 'score', 'descending': True}],
             'calculations': [
                 {'as': 'rn', 'op': 'row_number'},
                 {'as': 'run', 'op': 'running_sum', 'column': 'score'},
             ]},
            {'out': 'p', 'op': 'project', 'source': 'w',
             'columns': ['dept', 'score', 'r_tag', 'rn', 'run']},
            {'out': 'g', 'op': 'group', 'source': 'p', 'keys': ['dept'],
             'measures': [{'as': 'n', 'op': 'count_all'},
                          {'as': 'total', 'op': 'sum', 'column': 'score'}]},
            {'out': 's', 'op': 'sort', 'source': 'g',
             'order': [{'column': 'n', 'descending': True}]},
        ]

    def test_composed_pipeline_lineage_and_source_output(self):
        before_people = copy.deepcopy(self.people)
        before_lookup = copy.deepcopy(self.lookup)
        result = pipeline({'people': self.people, 'lookup': self.lookup},
                          self.steps, ['s', 'people'])
        self.assertEqual(result['results'][0], {
            'name': 's',
            'table': {'columns': ['dept', 'n', 'total'], 'rows': [['b', 5, 36]]},
        })
        self.assertEqual(result['results'][1]['table'], before_people)
        self.assertEqual(result['lineage'], {
            'f': ['lookup', 'people'],
            'g': ['lookup', 'people'],
            'j': ['lookup', 'people'],
            'p': ['lookup', 'people'],
            's': ['lookup', 'people'],
            'w': ['lookup', 'people'],
        })
        result['results'][0]['table']['rows'][0][0] = 'changed'
        result['results'][1]['table']['rows'][0][0] = 99
        self.assertEqual(self.people, before_people)
        self.assertEqual(self.lookup, before_lookup)

    def test_window_values_are_restored_to_input_order(self):
        result = execute({'people': self.people, 'lookup': self.lookup},
                         self.steps[:4], ['p'])
        self.assertEqual(result['results'][0]['table']['rows'], [
            ['b', 10, 'x', 1, 10],
            ['b', 10, 'y', 2, 20],
            ['b', 4, None, 5, 36],
            ['b', 6, 'x', 3, 26],
            ['b', 6, 'y', 4, 32],
        ])

    def test_empty_outputs_still_execute_every_step(self):
        result = execute({'people': self.people}, [
            {'out': 'p', 'op': 'project', 'source': 'people', 'columns': ['id']},
            {'out': 'f', 'op': 'filter', 'source': 'p',
             'conditions': [{'column': 'id', 'op': 'gt', 'value': 1}]},
        ], [])
        self.assertEqual(result['results'], [])
        self.assertEqual(result['lineage'], {'p': ['people'], 'f': ['people']})

    def test_invalid_sources_steps_and_outputs(self):
        with self.assertRaises(ValueError):
            execute({'a': T(['x'], [[1]]), 'bad': T([], [])}, [], [])
        with self.assertRaises(ValueError):
            execute({' a': T(['x'], [[1]])}, [], [])
        with self.assertRaises(ValueError):
            execute({'a': T(['x'], [[1]])}, [
                {'out': 'b', 'op': 'project', 'source': 'later', 'columns': ['x']},
            ], [])
        with self.assertRaises(ValueError):
            execute({'a': T(['x'], [[1]])}, [
                {'out': 'a', 'op': 'project', 'source': 'a', 'columns': ['x']},
            ], [])
        with self.assertRaises(ValueError):
            execute({'a': T(['x'], [[1]])}, [
                {'out': 'b', 'op': 'project', 'source': 'a', 'columns': ['x']},
                {'out': 'b', 'op': 'sort', 'source': 'a', 'order': []},
            ], [])
        with self.assertRaises(ValueError):
            execute({'a': T(['x'], [[1]])}, [
                {'out': 'b', 'op': 'nope', 'source': 'a'},
            ], [])
        with self.assertRaises(ValueError):
            execute({'a': T(['x'], [[1]])}, [
                {'out': 'b', 'op': 'project', 'source': 'a'},
            ], [])
        with self.assertRaises(ValueError):
            execute({'a': T(['x'], [[1]])}, [], ['missing'])
        with self.assertRaises(ValueError):
            execute({'a': T(['x'], [[1]])}, [], ['a', 'a'])


if __name__ == '__main__':
    unittest.main()
