import copy
import unittest

from reports import build_report


class BuildReportTests(unittest.TestCase):
    def test_counts_units_and_zero_keys(self):
        stock = {'B': 3, 'A': 2, 'zero': 0}
        orders = [{'id': 'a1', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'r1', 'items': [{'sku': 'B', 'qty': 5}]},
                  {'id': 'a2', 'items': [{'sku': 'B', 'qty': 1}]}]
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': {'B': 2, 'A': 0, 'zero': 0},
            'accepted_unique': 2,
            'rejected_unique': 1,
            'units_reserved': 3,
            'reserved_by_sku': {'A': 2, 'B': 1, 'zero': 0},
            'rejected_ids': ['r1'],
        })
        self.assertEqual(list(report['reserved_by_sku']), ['A', 'B', 'zero'])

    def test_replays_counted_once_and_consume_nothing(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], {'A': 3})
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 0)
        self.assertEqual(report['units_reserved'], 2)
        self.assertEqual(report['reserved_by_sku'], {'A': 2})
        self.assertEqual(report['rejected_ids'], [])

    def test_rejected_replay_counted_once_in_first_appearance_order(self):
        stock = {'A': 1}
        orders = [{'id': 'r2', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'ok', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'r1', 'items': [{'sku': 'nope', 'qty': 1}]},
                  {'id': 'r2', 'items': [{'sku': 'A', 'qty': 9}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 2)
        self.assertEqual(report['rejected_ids'], ['r2', 'r1'])
        self.assertEqual(report['remaining'], {'A': 0})
        self.assertEqual(report['units_reserved'], 1)

    def test_empty_orders(self):
        stock = {'A': 2, 'zero': 0}
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': []}]
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': {'A': 2, 'zero': 0},
            'accepted_unique': 1,
            'rejected_unique': 0,
            'units_reserved': 0,
            'reserved_by_sku': {'A': 0, 'zero': 0},
            'rejected_ids': [],
        })

    def test_conflict_raises_and_preserves_inputs(self):
        stock = {'A': 4, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_report_has_exact_keys_and_no_input_mutation(self):
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        report = build_report(stock, orders)
        self.assertEqual(set(report), {'remaining', 'accepted_unique', 'rejected_unique',
                                       'units_reserved', 'reserved_by_sku', 'rejected_ids'})
        self.assertEqual((stock, orders), before)
        report['remaining']['A'] = 99
        report['reserved_by_sku']['A'] = 99
        self.assertEqual(stock, {'A': 1})


if __name__ == '__main__':
    unittest.main()
