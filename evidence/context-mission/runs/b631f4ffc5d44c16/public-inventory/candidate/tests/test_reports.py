import copy
import unittest

from allocation import allocate
from reports import build_report


class BuildReportTests(unittest.TestCase):
    def test_exact_shape_and_values(self):
        stock = {'A': 5, 'B': 2, 'zero': 0}
        orders = [{'id': 'a1', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'b1', 'items': [{'sku': 'B', 'qty': 4}]},
                  {'id': 'a2', 'items': [{'sku': 'A', 'qty': 1}]}]
        report = build_report(stock, orders)
        self.assertEqual(set(report), {'remaining', 'accepted_unique', 'rejected_unique',
                                       'units_reserved', 'reserved_by_sku', 'rejected_ids'})
        self.assertEqual(report['remaining'], {'A': 2, 'B': 2, 'zero': 0})
        self.assertEqual(report['accepted_unique'], 2)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['units_reserved'], 3)
        self.assertEqual(report['rejected_ids'], ['b1'])

    def test_reserved_by_sku_sorted_with_zeros(self):
        stock = {'b': 1, 'a': 2, 'c': 0, 'A': 3}
        report = build_report(stock, [{'id': 'x', 'items': [{'sku': 'a', 'qty': 1}]}])
        self.assertEqual(list(report['reserved_by_sku']), ['A', 'a', 'b', 'c'])
        self.assertEqual(report['reserved_by_sku'], {'A': 0, 'a': 1, 'b': 0, 'c': 0})
        self.assertEqual(report['units_reserved'], 1)

    def test_replays_counted_once_and_consume_nothing(self):
        stock = {'A': 10}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 2}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], {'A': 7})
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 0)
        self.assertEqual(report['units_reserved'], 3)
        self.assertEqual(report['reserved_by_sku'], {'A': 3})

    def test_rejected_replay_counted_once_in_first_appearance_order(self):
        stock = {'A': 1}
        orders = [{'id': 'r1', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'r2', 'items': [{'sku': 'nope', 'qty': 1}]},
                  {'id': 'r1', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'ok', 'items': []}]
        report = build_report(stock, orders)
        self.assertEqual(report['rejected_ids'], ['r1', 'r2'])
        self.assertEqual(report['rejected_unique'], 2)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['remaining'], {'A': 1})
        self.assertEqual(report['units_reserved'], 0)

    def test_conflict_raises_and_does_not_mutate_inputs(self):
        stock = {'A': 4, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_does_not_mutate_inputs_on_success(self):
        stock = {'A': 3, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        report = build_report(stock, orders)
        self.assertEqual((stock, orders), before)
        self.assertIsNot(report['remaining'], stock)
        report['remaining']['A'] = 999
        report['reserved_by_sku']['A'] = 999
        self.assertEqual((stock, orders), before)

    def test_consistent_with_allocate(self):
        stock = {'A': 2, 'B': 1}
        orders = [{'id': 'p', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 2}]},
                  {'id': 'q', 'items': [{'sku': 'A', 'qty': 2}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], allocate(stock, orders)['remaining'])

    def test_empty_inputs(self):
        self.assertEqual(build_report({}, []), {'remaining': {}, 'accepted_unique': 0,
                                                'rejected_unique': 0, 'units_reserved': 0,
                                                'reserved_by_sku': {}, 'rejected_ids': []})


if __name__ == '__main__':
    unittest.main()
