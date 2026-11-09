import copy
import unittest

from allocation import allocate


class IdempotentAllocationTests(unittest.TestCase):
    def test_replay_reuses_outcome_without_consuming_stock(self):
        result = allocate({'A': 3}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                                     {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(result['remaining'], {'A': 1})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True},
                                             {'id': 'x', 'accepted': True}])

    def test_double_submit_does_not_double_decrement(self):
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 4}, orders)['remaining'], {'A': 2})

    def test_line_order_and_splitting_do_not_change_identity(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1}, {'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1}]}]
        result = allocate({'A': 1, 'B': 2}, orders)
        self.assertEqual(result['remaining'], {'A': 0, 'B': 0})
        self.assertEqual([r['accepted'] for r in result['results']], [True, True])

    def test_rejected_id_stays_rejected_on_late_replay(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 9}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 9}]}]
        stock = {'A': 5}
        with self.assertRaises(ValueError):
            allocate(stock, orders + [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        result = allocate(stock, orders)
        self.assertEqual(result['remaining'], {'A': 4})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': False},
                                             {'id': 'y', 'accepted': True},
                                             {'id': 'x', 'accepted': False}])

    def test_reusing_id_with_different_quantities_raises_and_preserves_inputs(self):
        stock = {'A': 2, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_sku_set_change_is_different_identity(self):
        stock = {'A': 5, 'B': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_empty_order_replay_and_conflict(self):
        stock = {'A': 1}
        replay = allocate(stock, [{'id': 'e', 'items': []}, {'id': 'e', 'items': []}])
        self.assertEqual(replay, {'remaining': {'A': 1},
                                  'results': [{'id': 'e', 'accepted': True},
                                              {'id': 'e', 'accepted': True}]})
        before = copy.deepcopy(stock)
        with self.assertRaises(ValueError):
            allocate(stock, [{'id': 'e', 'items': []},
                             {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(stock, before)

    def test_local_ids_independent_across_calls(self):
        self.assertEqual(allocate({'A': 1}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
                         ['results'][0]['accepted'], True)
        self.assertEqual(allocate({'A': 0}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
                         ['results'][0]['accepted'], False)

    def test_case_sensitive_skus_with_distinct_ids(self):
        stock = {'A': 1, 'a': 1}
        result = allocate(stock, [{'id': 'upper', 'items': [{'sku': 'A', 'qty': 1}]},
                                  {'id': 'lower', 'items': [{'sku': 'a', 'qty': 1}]}])
        self.assertEqual(result['remaining'], {'A': 0, 'a': 0})
        self.assertEqual(result['results'], [{'id': 'upper', 'accepted': True},
                                             {'id': 'lower', 'accepted': True}])
        self.assertEqual(stock, {'A': 1, 'a': 1})

    def test_case_differing_sku_is_different_identity(self):
        stock = {'A': 1, 'a': 1}
        before = copy.deepcopy(stock)
        with self.assertRaises(ValueError):
            allocate(stock, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                             {'id': 'x', 'items': [{'sku': 'a', 'qty': 1}]}])
        self.assertEqual(stock, before)

    def test_substituted_sku_is_different_identity(self):
        with self.assertRaises(ValueError):
            allocate({'A': 1}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                                {'id': 'x', 'items': [{'sku': 'B', 'qty': 1}]}])


if __name__ == '__main__':
    unittest.main()
