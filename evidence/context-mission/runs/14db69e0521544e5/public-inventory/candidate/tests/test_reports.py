import copy
import unittest

from reports import build_report


class BuildReportTests(unittest.TestCase):
    def test_report_fields_and_replay_accounting(self):
        stock = {'A': 3, 'B': 1, 'zero': 0}
        orders = [
            {'id': 'o1', 'items': [{'sku': 'A', 'qty': 2}]},
            {'id': 'o2', 'items': [{'sku': 'A', 'qty': 2}]},
            {'id': 'o1', 'items': [{'sku': 'A', 'qty': 2}]},
            {'id': 'o3', 'items': [{'sku': 'A', 'qty': 1}]},
        ]
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': {'A': 0, 'B': 1, 'zero': 0},
            'accepted_unique': 2,
            'rejected_unique': 1,
            'units_reserved': 3,
            'reserved_by_sku': {'A': 3, 'B': 0, 'zero': 0},
            'rejected_ids': ['o2'],
        })
        self.assertEqual(set(report), {
            'remaining', 'accepted_unique', 'rejected_unique',
            'units_reserved', 'reserved_by_sku', 'rejected_ids',
        })

    def test_line_order_and_splitting_replay_counted_once(self):
        stock = {'A': 4, 'B': 2}
        orders = [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                  {'sku': 'B', 'qty': 1}]},
            {'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                  {'sku': 'A', 'qty': 1}]},
            {'id': 'y', 'items': [{'sku': 'A', 'qty': 1},
                                  {'sku': 'A', 'qty': 1}]},
            {'id': 'y', 'items': [{'sku': 'A', 'qty': 2}]},
        ]
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': {'A': 1, 'B': 1},
            'accepted_unique': 2,
            'rejected_unique': 0,
            'units_reserved': 4,
            'reserved_by_sku': {'A': 3, 'B': 1},
            'rejected_ids': [],
        })

    def test_reserved_by_sku_uses_sorted_original_key_order(self):
        stock = {'z': 0, 'a': 2, 'm': 1, 'b': 0}
        report = build_report(stock, [
            {'id': 'x', 'items': [{'sku': 'a', 'qty': 2},
                                  {'sku': 'm', 'qty': 1}]},
        ])
        self.assertEqual(list(report['reserved_by_sku']), ['a', 'b', 'm', 'z'])
        self.assertEqual(report['reserved_by_sku'],
                         {'a': 2, 'b': 0, 'm': 1, 'z': 0})
        self.assertEqual(report['units_reserved'], 3)

    def test_rejected_ids_first_appearance_order(self):
        report = build_report({'A': 0}, [
            {'id': 'r2', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'r1', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'r2', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'e', 'items': []},
            {'id': 'r3', 'items': [{'sku': 'A', 'qty': 1}]},
        ])
        self.assertEqual(report['rejected_ids'], ['r2', 'r1', 'r3'])
        self.assertEqual(report['rejected_unique'], 3)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['reserved_by_sku'], {'A': 0})

    def test_conflicting_id_raises_and_preserves_inputs(self):
        stock = {'A': 3, 'zero': 0}
        orders = [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
        ]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_empty_orders_and_empty_order_identity(self):
        stock = {'A': 1, 'zero': 0}
        self.assertEqual(build_report(stock, []), {
            'remaining': {'A': 1, 'zero': 0},
            'accepted_unique': 0,
            'rejected_unique': 0,
            'units_reserved': 0,
            'reserved_by_sku': {'A': 0, 'zero': 0},
            'rejected_ids': [],
        })
        report = build_report(stock, [
            {'id': 'e', 'items': []},
            {'id': 'e', 'items': []},
        ])
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['units_reserved'], 0)

    def test_unknown_sku_rejection_and_zero_keys(self):
        report = build_report({'A': 1, 'B': 0}, [
            {'id': 'missing', 'items': [{'sku': 'NOPE', 'qty': 1}]},
            {'id': 'ok', 'items': [{'sku': 'A', 'qty': 1}]},
        ])
        self.assertEqual(report, {
            'remaining': {'A': 0, 'B': 0},
            'accepted_unique': 1,
            'rejected_unique': 1,
            'units_reserved': 1,
            'reserved_by_sku': {'A': 1, 'B': 0},
            'rejected_ids': ['missing'],
        })

    def test_inputs_unchanged_on_success(self):
        stock = {'A': 2, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        build_report(stock, orders)
        self.assertEqual((stock, orders), before)


if __name__ == '__main__':
    unittest.main()
