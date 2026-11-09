import copy
import unittest

from allocation import allocate
from reports import build_report

EXPECTED_KEYS = {'remaining', 'accepted_unique', 'rejected_unique',
                 'units_reserved', 'reserved_by_sku', 'rejected_ids'}


class BuildReportTests(unittest.TestCase):
    def test_exact_key_set(self):
        report = build_report({'A': 1}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(set(report), EXPECTED_KEYS)

    def test_basic_counts_and_units(self):
        stock = {'A': 5, 'B': 3}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'B', 'qty': 1}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 10}]}]
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': {'A': 3, 'B': 2},
            'accepted_unique': 1,
            'rejected_unique': 1,
            'units_reserved': 3,
            'reserved_by_sku': {'A': 2, 'B': 1},
            'rejected_ids': ['b'],
        })

    def test_equivalent_replays_counted_once_and_consume_nothing(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'B', 'qty': 1}]},
                  # same identity: reordered lines
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1}, {'sku': 'A', 'qty': 2}]},
                  # same identity: split lines
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1}]}]
        report = build_report({'A': 4, 'B': 2}, orders)
        self.assertEqual(report, {
            'remaining': {'A': 2, 'B': 1},
            'accepted_unique': 1,
            'rejected_unique': 0,
            'units_reserved': 3,
            'reserved_by_sku': {'A': 2, 'B': 1},
            'rejected_ids': [],
        })

    def test_rejected_replays_counted_once(self):
        orders = [{'id': 'bad', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'bad', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'good', 'items': [{'sku': 'A', 'qty': 1}]}]
        report = build_report({'A': 1}, orders)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['rejected_ids'], ['bad'])
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['units_reserved'], 1)
        self.assertEqual(report['remaining'], {'A': 0})

    def test_rejected_ids_first_appearance_order(self):
        orders = [{'id': 'z', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'm', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'z', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'a', 'items': [{'sku': 'A', 'qty': 9}]}]
        self.assertEqual(build_report({'A': 1}, orders)['rejected_ids'], ['z', 'm', 'a'])

    def test_reserved_by_sku_includes_zeros_in_sorted_order(self):
        stock = {'b': 2, 'a': 0, 'zero': 0, 'C': 4}
        orders = [{'id': 'x', 'items': [{'sku': 'b', 'qty': 1}, {'sku': 'C', 'qty': 3}]}]
        report = build_report(stock, orders)
        self.assertEqual(list(report['reserved_by_sku']), ['C', 'a', 'b', 'zero'])
        self.assertEqual(report['reserved_by_sku'], {'C': 3, 'a': 0, 'b': 1, 'zero': 0})
        self.assertEqual(report['units_reserved'], 4)

    def test_unknown_sku_rejection_reserves_nothing(self):
        report = build_report({'A': 3}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                                               {'sku': 'nope', 'qty': 1}]}])
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['reserved_by_sku'], {'A': 0})
        self.assertEqual(report['remaining'], {'A': 3})
        self.assertEqual(report['rejected_ids'], ['x'])

    def test_empty_order_counts_as_accepted_unique(self):
        report = build_report({'A': 0}, [{'id': 'e', 'items': []}, {'id': 'e', 'items': []}])
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 0)
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['remaining'], {'A': 0})

    def test_conflict_raises_and_inputs_unchanged(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_success_path_inputs_unchanged(self):
        stock = {'A': 4, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        report = build_report(stock, orders)
        self.assertEqual((stock, orders), before)
        report['remaining']['A'] = 99
        self.assertEqual(stock, {'A': 4, 'zero': 0})

    def test_remaining_matches_allocate(self):
        stock = {'A': 3, 'B': 2}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'y', 'items': [{'sku': 'B', 'qty': 5}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(build_report(stock, orders)['remaining'], allocate(stock, orders)['remaining'])


if __name__ == '__main__':
    unittest.main()
