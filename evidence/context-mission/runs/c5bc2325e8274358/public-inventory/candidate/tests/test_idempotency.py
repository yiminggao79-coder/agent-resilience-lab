import copy
import unittest

from allocation import allocate


class IdempotentOrderIdTests(unittest.TestCase):
    def test_replay_of_accepted_order_consumes_no_extra_stock(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': {'A': 3},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'x', 'accepted': True}]})
        self.assertEqual(stock, {'A': 5})

    def test_replay_of_rejected_order_stays_rejected(self):
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'x', 'accepted': False},
                                      {'id': 'x', 'accepted': False}]})

    def test_rejected_replay_stays_rejected_after_stock_freed(self):
        # The earlier decision is replayed, not re-evaluated against stock.
        stock = {'A': 2}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        result = allocate(stock, orders)
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': False},
                                             {'id': 'y', 'accepted': True},
                                             {'id': 'x', 'accepted': False}])
        self.assertEqual(result['remaining'], {'A': 1})

    def test_line_order_and_line_splitting_do_not_change_identity(self):
        stock = {'A': 3, 'B': 4}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 2, 'B': 2},
                          'results': [{'id': 'x', 'accepted': True},
                                      {'id': 'x', 'accepted': True}]})

    def test_different_quantities_raise_value_error(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual(stock, {'A': 5})

    def test_value_error_does_not_mutate_inputs(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]},
                  {'id': 'z', 'items': []}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_different_sku_sets_raise_value_error(self):
        stock = {'A': 5, 'B': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1}]}]
        with self.assertRaises(ValueError):
            allocate(stock, orders)

    def test_unknown_sku_replay_is_rejected_without_change(self):
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'NEW', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'NEW', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'x', 'accepted': False},
                                      {'id': 'x', 'accepted': False}]})

    def test_empty_orders_follow_identity_rule(self):
        stock = {'A': 1}
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': []},
                  {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}]
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual(allocate(stock, [{'id': 'e', 'items': []},
                                          {'id': 'e', 'items': []}]),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'e', 'accepted': True},
                                      {'id': 'e', 'accepted': True}]})

    def test_empty_vs_nonempty_same_id_raises(self):
        with self.assertRaises(ValueError):
            allocate({'A': 1}, [{'id': 'e', 'items': []},
                                {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])

    def test_distinct_ids_are_independent(self):
        stock = {'A': 2}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'z', 'items': [{'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders)['results'],
                         [{'id': 'x', 'accepted': True},
                          {'id': 'y', 'accepted': True},
                          {'id': 'z', 'accepted': False}])

    def test_results_rows_are_fresh_dicts(self):
        stock = {'A': 2}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        result = allocate(stock, orders)
        self.assertIsNot(result['results'][0], result['results'][1])
        result['results'][0]['accepted'] = 'mutated'
        self.assertTrue(result['results'][1]['accepted'])


if __name__ == '__main__':
    unittest.main()
