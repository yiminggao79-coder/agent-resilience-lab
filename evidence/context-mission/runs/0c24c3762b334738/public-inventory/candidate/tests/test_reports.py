import copy
import unittest

from allocation import allocate
from reports import build_report


class BuildReportTests(unittest.TestCase):
    def test_exact_shape_and_values(self):
        stock = {'B': 5, 'A': 3, 'Zero': 0}
        orders = [{'id': 'a1', 'items': [{'sku': 'A', 'qty': 2},
                                         {'sku': 'B', 'qty': 1}]},
                  {'id': 'r1', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'a2', 'items': []}]
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': {'B': 4, 'A': 1, 'Zero': 0},
            'accepted_unique': 2,
            'rejected_unique': 1,
            'units_reserved': 3,
            'reserved_by_sku': {'A': 2, 'B': 1, 'Zero': 0},
            'rejected_ids': ['r1'],
        })
        self.assertEqual(list(report), ['remaining', 'accepted_unique',
                                        'rejected_unique', 'units_reserved',
                                        'reserved_by_sku', 'rejected_ids'])
        self.assertEqual(list(report['reserved_by_sku']), ['A', 'B', 'Zero'])

    def test_replays_counted_once_and_consume_nothing(self):
        stock = {'A': 10}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 4}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2},
                                        {'sku': 'A', 'qty': 2}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 99}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 99}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 99}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['units_reserved'], 4)
        self.assertEqual(report['reserved_by_sku'], {'A': 4})
        self.assertEqual(report['remaining'], {'A': 6})
        self.assertEqual(report['rejected_ids'], ['y'])

    def test_rejected_ids_in_first_appearance_order(self):
        stock = {'A': 1}
        orders = [{'id': 'z', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'm', 'items': [{'sku': 'nope', 'qty': 1}]},
                  {'id': 'z', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'a', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'b', 'items': [{'sku': 'nope', 'qty': 1}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['rejected_ids'], ['z', 'm', 'a', 'b'])
        self.assertEqual(report['rejected_unique'], 4)
        self.assertEqual(report['accepted_unique'], 0)
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['remaining'], {'A': 1})

    def test_first_decision_is_what_counts(self):
        # An ID accepted once then replayed stays accepted and is not rejected,
        # even if later stock could no longer cover it.
        stock = {'A': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['rejected_ids'], ['y'])
        self.assertEqual(report['units_reserved'], 3)

    def test_empty_inputs(self):
        self.assertEqual(build_report({}, []), {
            'remaining': {},
            'accepted_unique': 0,
            'rejected_unique': 0,
            'units_reserved': 0,
            'reserved_by_sku': {},
            'rejected_ids': [],
        })

    def test_conflict_raises_and_inputs_unchanged(self):
        stock = {'A': 5, 'Z': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 4}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_inputs_unchanged_on_success_and_output_detached(self):
        stock = {'A': 2, 'Zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        report = build_report(stock, orders)
        self.assertEqual((stock, orders), before)
        report['remaining']['A'] = 99
        report['reserved_by_sku']['A'] = 99
        self.assertEqual((stock, orders), before)

    def test_agrees_with_allocate_on_remaining(self):
        stock = {'A': 4, 'B': 0, 'C': 7}
        orders = [{'id': 'one', 'items': [{'sku': 'A', 'qty': 4}]},
                  {'id': 'two', 'items': [{'sku': 'C', 'qty': 8}]},
                  {'id': 'one', 'items': [{'sku': 'A', 'qty': 2},
                                          {'sku': 'A', 'qty': 2}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], allocate(stock, orders)['remaining'])
        self.assertEqual(report['reserved_by_sku'], {'A': 4, 'B': 0, 'C': 0})
        self.assertEqual(report['units_reserved'], 4)


if __name__ == '__main__':
    unittest.main()
