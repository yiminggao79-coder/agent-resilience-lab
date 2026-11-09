import copy
import unittest

from allocation import allocate
from reports import build_report


class BuildReportTests(unittest.TestCase):
    def test_basic_report_shape_and_values(self):
        stock = {'B': 5, 'A': 3, 'zero': 0}
        orders = [{'id': 'first', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'second', 'items': [{'sku': 'B', 'qty': 9}]},
                  {'id': 'third', 'items': [{'sku': 'B', 'qty': 1},
                                            {'sku': 'B', 'qty': 2}]},
                  {'id': 'fourth', 'items': []}]
        expected_remaining = {'B': 2, 'A': 1, 'zero': 0}
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': expected_remaining,
            'accepted_unique': 3,
            'rejected_unique': 1,
            'units_reserved': 5,
            'reserved_by_sku': {'A': 2, 'B': 3, 'zero': 0},
            'rejected_ids': ['second'],
        })
        self.assertEqual(list(report['reserved_by_sku']), ['A', 'B', 'zero'])
        self.assertEqual(set(report), {'remaining', 'accepted_unique',
                                       'rejected_unique', 'units_reserved',
                                       'reserved_by_sku', 'rejected_ids'})

    def test_report_matches_allocate_remaining(self):
        stock = {'A': 2, 'B': 1}
        orders = [{'id': '1', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 2}]},
                  {'id': '2', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(build_report(stock, orders)['remaining'],
                         allocate(stock, orders)['remaining'])

    def test_equivalent_replays_counted_once(self):
        stock = {'A': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 1}]}]
        self.assertEqual(build_report(stock, orders), {
            'remaining': {'A': 1},
            'accepted_unique': 1,
            'rejected_unique': 0,
            'units_reserved': 2,
            'reserved_by_sku': {'A': 2},
            'rejected_ids': [],
        })

    def test_recovered_rejection_still_rejected_for_report(self):
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 5}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], {'A': 1})
        self.assertEqual(report['accepted_unique'], 0)
        self.assertEqual(report['rejected_unique'], 2)
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['reserved_by_sku'], {'A': 0})
        self.assertEqual(report['rejected_ids'], ['x', 'y'])

    def test_rejected_order_consumes_nothing(self):
        stock = {'A': 2, 'B': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 2}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], stock)
        self.assertEqual(report['reserved_by_sku'], {'A': 0, 'B': 0})
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['rejected_ids'], ['x'])

    def test_conflict_raises_and_inputs_unchanged(self):
        stock = {'A': 3, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_empty_orders_and_stock(self):
        self.assertEqual(build_report({}, []), {
            'remaining': {},
            'accepted_unique': 0,
            'rejected_unique': 0,
            'units_reserved': 0,
            'reserved_by_sku': {},
            'rejected_ids': [],
        })
        self.assertEqual(build_report({'A': 0}, [{'id': 'e', 'items': []}]), {
            'remaining': {'A': 0},
            'accepted_unique': 1,
            'rejected_unique': 0,
            'units_reserved': 0,
            'reserved_by_sku': {'A': 0},
            'rejected_ids': [],
        })

    def test_unknown_sku_rejected_without_phantom_key(self):
        report = build_report({'A': 1}, [{'id': 'x', 'items': [{'sku': 'Z', 'qty': 1}]}])
        self.assertEqual(report['remaining'], {'A': 1})
        self.assertEqual(report['reserved_by_sku'], {'A': 0})
        self.assertEqual(report['rejected_ids'], ['x'])

    def test_empty_order_replay_conflict_raises(self):
        stock = {'A': 1}
        before = copy.deepcopy(stock)
        with self.assertRaises(ValueError):
            build_report(stock, [{'id': 'e', 'items': []},
                                 {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(stock, before)


if __name__ == '__main__':
    unittest.main()
