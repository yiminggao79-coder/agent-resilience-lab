import copy
import unittest

from reports import build_report


class BuildReportTests(unittest.TestCase):
    def test_mixed_outcome_summary(self):
        stock = {'B': 5, 'A': 2, 'zero': 0}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'r1', 'items': [{'sku': 'B', 'qty': 6}]},
                  {'id': 'r2', 'items': [{'sku': 'nope', 'qty': 1}]},
                  {'id': 'b', 'items': [{'sku': 'B', 'qty': 2},
                                        {'sku': 'B', 'qty': 1}]}]
        report = build_report(stock, orders)
        self.assertEqual(report, {
            'remaining': {'B': 2, 'A': 0, 'zero': 0},
            'accepted_unique': 2,
            'rejected_unique': 2,
            'units_reserved': 5,
            'reserved_by_sku': {'A': 2, 'B': 3, 'zero': 0},
            'rejected_ids': ['r1', 'r2'],
        })

    def test_reserved_by_sku_is_sorted_and_includes_zero_keys(self):
        stock = {'zeta': 0, 'mike': 4, 'alpha': 1, 'Beta': 2}
        orders = [{'id': 'x', 'items': [{'sku': 'mike', 'qty': 1}]}]
        report = build_report(stock, orders)
        self.assertEqual(list(report['reserved_by_sku']), ['Beta', 'alpha', 'mike', 'zeta'])
        self.assertEqual(report['reserved_by_sku'],
                         {'Beta': 0, 'alpha': 0, 'mike': 1, 'zeta': 0})
        self.assertEqual(report['units_reserved'], 1)
        self.assertEqual(report['remaining'],
                         {'zeta': 0, 'mike': 3, 'alpha': 1, 'Beta': 2})

    def test_empty_inputs(self):
        self.assertEqual(build_report({}, []), {
            'remaining': {},
            'accepted_unique': 0,
            'rejected_unique': 0,
            'units_reserved': 0,
            'reserved_by_sku': {},
            'rejected_ids': [],
        })

    def test_replays_count_once_and_consume_nothing(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 9}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['remaining'], {'A': 3})
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['units_reserved'], 2)
        self.assertEqual(report['reserved_by_sku'], {'A': 2})
        self.assertEqual(report['rejected_ids'], ['y'])

    def test_rejected_replay_after_stock_changes_stays_rejected(self):
        stock = {'A': 2}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        report = build_report(stock, orders)
        self.assertEqual(report['rejected_ids'], ['x'])
        self.assertEqual(report['rejected_unique'], 1)
        self.assertEqual(report['accepted_unique'], 0)
        self.assertEqual(report['units_reserved'], 0)

    def test_empty_order_ids_follow_identity_rule(self):
        report = build_report({'A': 1}, [{'id': 'e', 'items': []},
                                         {'id': 'e', 'items': []}])
        self.assertEqual(report['accepted_unique'], 1)
        self.assertEqual(report['rejected_unique'], 0)
        self.assertEqual(report['units_reserved'], 0)
        self.assertEqual(report['remaining'], {'A': 1})
        with self.assertRaises(ValueError):
            build_report({'A': 1}, [{'id': 'e', 'items': []},
                                    {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])

    def test_conflicting_payload_raises_without_mutation(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            build_report(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_no_mutation_on_success(self):
        stock = {'A': 3, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'y', 'items': [{'sku': 'missing', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        report = build_report(stock, orders)
        self.assertEqual((stock, orders), before)
        report['remaining']['A'] = 999
        report['reserved_by_sku']['A'] = 999
        report['rejected_ids'].append('junk')
        self.assertEqual((stock, orders), before)

    def test_report_matches_allocate(self):
        from allocation import allocate

        stock = {'A': 4, 'B': 1, 'zero': 0}
        orders = [{'id': '1', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1}]},
                  {'id': '2', 'items': [{'sku': 'B', 'qty': 1}]},
                  {'id': '1', 'items': [{'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]}]
        self.assertEqual(build_report(stock, orders)['remaining'],
                         allocate(stock, orders)['remaining'])


if __name__ == '__main__':
    unittest.main()
