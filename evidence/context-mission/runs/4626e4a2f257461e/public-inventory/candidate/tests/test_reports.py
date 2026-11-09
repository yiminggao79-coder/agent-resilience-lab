import copy
import unittest

from reports import build_report


class ReportTests(unittest.TestCase):
    def test_full_report_shape_and_values(self):
        stock = {'B': 5, 'A': 4, 'zero': 0}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'r', 'items': [{'sku': 'B', 'qty': 9}]},
                  {'id': 'b', 'items': [{'sku': 'B', 'qty': 1}]}]
        self.assertEqual(build_report(stock, orders), {
            'remaining': {'B': 4, 'A': 2, 'zero': 0},
            'accepted_unique': 2,
            'rejected_unique': 1,
            'units_reserved': 3,
            'reserved_by_sku': {'A': 2, 'B': 1, 'zero': 0},
            'rejected_ids': ['r'],
        })

    def test_exact_key_set(self):
        report = build_report({'A': 1}, [{'id': 'x', 'items': []}])
        self.assertEqual(set(report), {'remaining', 'accepted_unique',
                                       'rejected_unique', 'units_reserved',
                                       'reserved_by_sku', 'rejected_ids'})

    def test_replays_counted_once_and_consume_nothing(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        report = build_report({'A': 10}, orders)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['units_reserved'], 2)
        self.assertEqual(report['reserved_by_sku'], {'A': 2})
        self.assertEqual(report['remaining'], {'A': 8})

    def test_rejected_replay_counted_once_and_keeps_order(self):
        orders = [{'id': 'z', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'y', 'items': [{'sku': 'nope', 'qty': 1}]},
                  {'id': 'z', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'y', 'items': [{'sku': 'nope', 'qty': 1}]}]
        report = build_report({'A': 1}, orders)
        self.assertEqual(report['rejected_ids'], ['z', 'y'])
        self.assertEqual(report['rejected_unique'], 2)
        self.assertEqual(report['accepted_unique'], 0)
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['reserved_by_sku'], {'A': 0})
        self.assertEqual(report['remaining'], {'A': 1})

    def test_reserved_by_sku_sorted_and_includes_zero_and_unknown_produced_keys(self):
        stock = {'c': 0, 'a': 3, 'b': 2}
        orders = [{'id': 'x', 'items': [{'sku': 'a', 'qty': 2}]}]
        report = build_report(stock, orders)
        self.assertEqual(list(report['reserved_by_sku']), ['a', 'b', 'c'])
        self.assertEqual(report['reserved_by_sku'], {'a': 2, 'b': 0, 'c': 0})

    def test_case_sensitive_skus(self):
        stock = {'A': 1, 'a': 1}
        report = build_report(stock, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(report['reserved_by_sku'], {'A': 1, 'a': 0})
        self.assertEqual(report['remaining'], {'A': 0, 'a': 1})

    def test_conflicting_id_raises_and_inputs_unchanged(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_inputs_unchanged_on_success_and_output_detached(self):
        stock = {'A': 2}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        report = build_report(stock, orders)
        self.assertEqual((stock, orders), before)
        report['remaining']['A'] = 99
        report['reserved_by_sku']['A'] = 99
        self.assertEqual(stock, before[0])

    def test_consistent_with_allocate(self):
        from allocation import allocate
        stock = {'A': 3, 'B': 1}
        orders = [{'id': 'p', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'q', 'items': [{'sku': 'B', 'qty': 2}]},
                  {'id': 'p', 'items': [{'sku': 'A', 'qty': 2}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], allocate(stock, orders)['remaining'])


if __name__ == '__main__':
    unittest.main()
