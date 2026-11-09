import copy
import unittest

from allocation import allocate
from reports import build_report


class BuildReportTests(unittest.TestCase):
    def test_full_report_fields_and_values(self):
        stock = {'B': 1, 'A': 5, 'zero': 0}
        orders = [{'id': 'o1', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'o2', 'items': [{'sku': 'B', 'qty': 2}]},
                  {'id': 'o3', 'items': [{'sku': 'A', 'qty': 1}]}]
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': {'B': 1, 'A': 2, 'zero': 0},
            'accepted_unique': 2,
            'rejected_unique': 1,
            'units_reserved': 3,
            'reserved_by_sku': {'A': 3, 'B': 0, 'zero': 0},
            'rejected_ids': ['o2'],
        })

    def test_exact_key_set(self):
        report = build_report({}, [])
        self.assertEqual(set(report), {'remaining', 'accepted_unique',
                                       'rejected_unique', 'units_reserved',
                                       'reserved_by_sku', 'rejected_ids'})
        self.assertEqual(report['remaining'], {})
        self.assertEqual(report['reserved_by_sku'], {})
        self.assertEqual(report['units_reserved'], 0)

    def test_reserved_by_sku_inserted_in_sorted_key_order(self):
        report = build_report({'c': 3, 'a': 3, 'b': 3}, [])
        self.assertEqual(list(report['reserved_by_sku']), ['a', 'b', 'c'])

    def test_case_sensitive_zero_keys_included(self):
        stock = {'a': 0, 'A': 4, 'other': 0}
        report = build_report(stock, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 4}]}])
        self.assertEqual(report['reserved_by_sku'], {'A': 4, 'a': 0, 'other': 0})
        self.assertEqual(report['units_reserved'], 4)
        self.assertEqual(report['accepted_unique'], 1)

    def test_replays_count_once_and_consume_nothing(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        report = build_report({'A': 5}, orders)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 0)
        self.assertEqual(report['units_reserved'], 2)
        self.assertEqual(report['reserved_by_sku'], {'A': 2})
        self.assertEqual(report['remaining'], {'A': 3})
        self.assertEqual(report['rejected_ids'], [])

    def test_replayed_rejection_counted_once(self):
        orders = [{'id': 'r', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'ok', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'r', 'items': [{'sku': 'A', 'qty': 9}]}]
        report = build_report({'A': 1}, orders)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['rejected_ids'], ['r'])
        self.assertEqual(report['units_reserved'], 1)

    def test_rejected_ids_first_appearance_order(self):
        orders = [{'id': 'z', 'items': [{'sku': 'M', 'qty': 1}]},
                  {'id': 'm', 'items': [{'sku': 'M', 'qty': 1}]},
                  {'id': 'a', 'items': [{'sku': 'M', 'qty': 1}]}]
        report = build_report({}, orders)
        self.assertEqual(report['rejected_ids'], ['z', 'm', 'a'])

    def test_unknown_sku_rejected_and_not_in_reserved(self):
        report = build_report({'A': 2}, [{'id': 'u', 'items': [{'sku': 'Q', 'qty': 1}]}])
        self.assertEqual(report['remaining'], {'A': 2})
        self.assertEqual(report['reserved_by_sku'], {'A': 0})
        self.assertEqual(report['rejected_ids'], ['u'])

    def test_empty_orders_accepted_and_unique(self):
        orders = [{'id': 'e', 'items': []}, {'id': 'e', 'items': []},
                  {'id': 'f', 'items': []}]
        report = build_report({'A': 1}, orders)
        self.assertEqual(report['accepted_unique'], 2)
        self.assertEqual(report['rejected_unique'], 0)
        self.assertEqual(report['units_reserved'], 0)

    def test_conflicting_payload_raises_and_inputs_unchanged(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_no_input_mutation_and_detached_result(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        report = build_report(stock, orders)
        self.assertEqual((stock, orders), before)
        report['remaining']['A'] = 999
        report['reserved_by_sku']['A'] = 999
        self.assertEqual((stock, orders), before)

    def test_consistent_with_allocate(self):
        stock = {'A': 5, 'B': 2, 'C': 0}
        orders = [{'id': 'p', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'q', 'items': [{'sku': 'B', 'qty': 3}]},
                  {'id': 'r', 'items': [{'sku': 'C', 'qty': 1}]}]
        report = build_report(stock, orders)
        outcome = allocate(stock, orders)
        self.assertEqual(report['remaining'], outcome['remaining'])
        self.assertEqual(report['rejected_ids'],
                         [row['id'] for row in outcome['results']
                          if not row['accepted']])


if __name__ == '__main__':
    unittest.main()
