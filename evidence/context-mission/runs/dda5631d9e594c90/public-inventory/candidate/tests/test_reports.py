import copy
import unittest

from allocation import allocate
from reports import build_report


class BuildReportTests(unittest.TestCase):
    def test_exact_report_shape_and_values(self):
        stock = {'B': 5, 'A': 2, 'zero': 0}
        orders = [{'id': 'ok', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'B', 'qty': 1}]},
                  {'id': 'no', 'items': [{'sku': 'B', 'qty': 9}]},
                  {'id': 'empty', 'items': []}]
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': {'B': 4, 'A': 0, 'zero': 0},
            'accepted_unique': 2,
            'rejected_unique': 1,
            'units_reserved': 3,
            'reserved_by_sku': {'A': 2, 'B': 1, 'zero': 0},
            'rejected_ids': ['no'],
        })
        self.assertEqual(list(report), ['remaining', 'accepted_unique', 'rejected_unique',
                                        'units_reserved', 'reserved_by_sku', 'rejected_ids'])

    def test_reserved_by_sku_is_sorted_with_zero_and_unknown_keys(self):
        stock = {'zz': 0, 'm': 4, 'Aa': 3, 'a': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'm', 'qty': 2}, {'sku': 'ghost', 'qty': 1}]}]
        report = build_report(stock, orders)
        self.assertEqual(list(report['reserved_by_sku']), sorted(stock))
        self.assertEqual(report['reserved_by_sku'], {'Aa': 0, 'a': 0, 'm': 0, 'zz': 0})
        self.assertEqual(report['remaining'], {'zz': 0, 'm': 4, 'Aa': 3, 'a': 1})
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['rejected_ids'], ['x'])

    def test_equivalent_replays_counted_once_and_consume_nothing(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'a', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 1}]},
                  {'id': 'r', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'r', 'items': [{'sku': 'A', 'qty': 9}]}]
        report = build_report({'A': 5}, orders)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['units_reserved'], 2)
        self.assertEqual(report['reserved_by_sku'], {'A': 2})
        self.assertEqual(report['remaining'], {'A': 3})
        self.assertEqual(report['rejected_ids'], ['r'])

    def test_rejected_replay_after_stock_freed_still_counts_rejected(self):
        # Stock 3 cannot cover 'r' (needs 5) on first sight, so it is rejected.
        # The replay, though split into 3+2, must stay rejected by identity and
        # consume nothing even though it is otherwise well-formed.
        orders = [{'id': 'r', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'r', 'items': [{'sku': 'A', 'qty': 3}, {'sku': 'A', 'qty': 2}]}]
        report = build_report({'A': 3}, orders)
        self.assertEqual(report['accepted_unique'], 0)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['reserved_by_sku'], {'A': 0})
        self.assertEqual(report['remaining'], {'A': 3})
        self.assertEqual(report['rejected_ids'], ['r'])

    def test_rejected_ids_use_first_appearance_order(self):
        orders = [{'id': 'z', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'ok', 'items': []},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'z', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'm', 'items': [{'sku': 'nope', 'qty': 1}]}]
        report = build_report({'A': 1}, orders)
        self.assertEqual(report['rejected_ids'], ['z', 'b', 'm'])
        self.assertEqual(report['rejected_unique'], 3)
        self.assertEqual(report['accepted_unique'], 1)

    def test_matches_allocate_engine_exactly(self):
        stock = {'A': 3, 'B': 2}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'b', 'items': [{'sku': 'B', 'qty': 2}]},
                  {'id': 'c', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'a', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 1}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], allocate(stock, orders)['remaining'])
        # 'a' takes 2 A, 'b' takes 2 B, then 'c' needs 2 A but only 1 is left.
        self.assertEqual(report['remaining'], {'A': 1, 'B': 0})
        self.assertEqual(report['reserved_by_sku'], {'A': 2, 'B': 2})
        self.assertEqual(report['units_reserved'], 4)
        self.assertEqual(report['accepted_unique'], 2)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['rejected_ids'], ['c'])

    def test_empty_order_id_counts_as_accepted(self):
        report = build_report({'A': 0}, [{'id': 'e', 'items': []}, {'id': 'e', 'items': []}])
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 0)
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['reserved_by_sku'], {'A': 0})
        self.assertEqual(report['rejected_ids'], [])

    def test_conflict_raises_and_inputs_unchanged(self):
        stock = {'A': 5}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_inputs_unchanged_and_report_detached(self):
        stock = {'A': 4, 'zero': 0}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 3}]}]
        before = copy.deepcopy((stock, orders))
        report = build_report(stock, orders)
        self.assertEqual((stock, orders), before)
        report['remaining']['A'] = 999
        report['reserved_by_sku']['zero'] = 999
        self.assertEqual((stock, orders), before)


if __name__ == '__main__':
    unittest.main()
