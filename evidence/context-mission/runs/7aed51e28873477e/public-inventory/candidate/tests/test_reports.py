import copy
import unittest

from reports import build_report


class BuildReportTests(unittest.TestCase):
    def test_empty_inputs(self):
        self.assertEqual(build_report({}, []), {
            'remaining': {},
            'accepted_unique': 0,
            'rejected_unique': 0,
            'units_reserved': 0,
            'reserved_by_sku': {},
            'rejected_ids': [],
        })

    def test_exact_keys(self):
        report = build_report({'A': 1}, [{'id': 'o', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(set(report), {'remaining', 'accepted_unique', 'rejected_unique',
                                       'units_reserved', 'reserved_by_sku', 'rejected_ids'})

    def test_counts_and_units(self):
        stock = {'A': 5, 'B': 2, 'zero': 0}
        orders = [{'id': 'one', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'two', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 2}]},
                  {'id': 'three', 'items': [{'sku': 'A', 'qty': 99}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], {'A': 2, 'B': 0, 'zero': 0})
        self.assertEqual(report['accepted_unique'], 2)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['units_reserved'], 5)
        self.assertEqual(report['rejected_ids'], ['three'])

    def test_reserved_by_sku_sorted_with_zeros(self):
        stock = {'b': 4, 'A': 3, 'zero': 0, 'a': 1}
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'b', 'qty': 1}]}]
        report = build_report(stock, orders)
        self.assertEqual(list(report['reserved_by_sku']), ['A', 'a', 'b', 'zero'])
        self.assertEqual(report['reserved_by_sku'],
                         {'A': 2, 'a': 0, 'b': 1, 'zero': 0})
        self.assertEqual(report['units_reserved'], 3)

    def test_replays_counted_once_and_consume_nothing(self):
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'o', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 1}]}]
        report = build_report({'A': 5}, orders)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 0)
        self.assertEqual(report['units_reserved'], 2)
        self.assertEqual(report['reserved_by_sku'], {'A': 2})
        self.assertEqual(report['remaining'], {'A': 3})

    def test_rejected_replay_counted_once_and_stays_rejected(self):
        orders = [{'id': 'dup', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'dup', 'items': [{'sku': 'A', 'qty': 3}]}]
        report = build_report({'A': 2}, orders)
        self.assertEqual(report['accepted_unique'], 0)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['rejected_ids'], ['dup'])
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['remaining'], {'A': 2})

    def test_rejected_ids_first_appearance_order(self):
        stock = {'A': 1}
        orders = [{'id': 'z', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'k', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'm', 'items': [{'sku': 'missing', 'qty': 1}]},
                  {'id': 'z', 'items': [{'sku': 'A', 'qty': 2}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['rejected_ids'], ['z', 'm'])
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 2)

    def test_empty_orders_are_accepted_and_counted_once(self):
        orders = [{'id': 'e', 'items': []}, {'id': 'e', 'items': []}]
        report = build_report({'A': 0}, orders)
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 0)
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['reserved_by_sku'], {'A': 0})

    def test_conflict_raises_and_inputs_unchanged(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_no_mutation_and_detached_result(self):
        stock = {'A': 5}
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        report = build_report(stock, orders)
        self.assertEqual((stock, orders), before)
        report['remaining']['A'] = 123
        report['reserved_by_sku']['A'] = 456
        self.assertEqual(stock, {'A': 5})
        self.assertEqual((stock, orders), before)

    def test_matches_allocate_output(self):
        from allocation import allocate
        stock = {'A': 3, 'B': 3, 'C': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'y', 'items': [{'sku': 'C', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'z', 'items': [{'sku': 'B', 'qty': 1}]}]
        self.assertEqual(build_report(stock, orders)['remaining'],
                         allocate(stock, orders)['remaining'])


if __name__ == '__main__':
    unittest.main()
