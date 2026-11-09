import copy
import unittest

from allocation import allocate


class IdempotentOrderIdTests(unittest.TestCase):
    def test_replay_does_not_consume_stock_twice(self):
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 3}, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'o', 'accepted': True},
                                      {'id': 'o', 'accepted': True}]})

    def test_line_order_and_splitting_do_not_change_identity(self):
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 3}, {'sku': 'B', 'qty': 1}]},
                  {'id': 'o', 'items': [{'sku': 'B', 'qty': 1}, {'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 5, 'B': 5}, orders),
                         {'remaining': {'A': 2, 'B': 4},
                          'results': [{'id': 'o', 'accepted': True},
                                      {'id': 'o', 'accepted': True}]})

    def test_rejected_id_stays_rejected_on_replay(self):
        # Stock arrives between the two lines of the duplicate ID via another order.
        orders = [{'id': 'dup', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'filler', 'items': []},
                  {'id': 'dup', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 1}, orders)
        self.assertEqual(result,
                         {'remaining': {'A': 1},
                          'results': [{'id': 'dup', 'accepted': False},
                                      {'id': 'filler', 'accepted': True},
                                      {'id': 'dup', 'accepted': False}]})

    def test_replay_rejection_does_not_consult_later_state(self):
        # The replay must reproduce the stored verdict, not re-evaluate stock.
        orders = [{'id': 'dup', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'dup', 'items': [{'sku': 'A', 'qty': 3}]}]
        self.assertEqual(allocate({'A': 2}, orders),
                         {'remaining': {'A': 2},
                          'results': [{'id': 'dup', 'accepted': False},
                                      {'id': 'dup', 'accepted': False}]})

    def test_same_id_different_quantities_raises_and_keeps_inputs(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'o', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_different_sku_set_is_a_conflict(self):
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'o', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'C', 'qty': 1}]}]
        with self.assertRaises(ValueError):
            allocate({'A': 3, 'C': 3}, orders)

    def test_explicit_zero_line_is_a_different_combined_quantities_map(self):
        # Combined quantities differ ({'A': 1} vs {'A': 1, 'C': 0}).
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'o', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'C', 'qty': 0}]}]
        with self.assertRaises(ValueError):
            allocate({'A': 3}, orders)

    def test_empty_orders_follow_identity_rule(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': []}]
        self.assertEqual(allocate({'A': 1}, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'e', 'accepted': True},
                                      {'id': 'e', 'accepted': True}]})
        with self.assertRaises(ValueError):
            allocate({'A': 1}, [{'id': 'e', 'items': []},
                                {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])

    def test_distinct_ids_are_independent(self):
        orders = [{'id': 'one', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'two', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 3}, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'one', 'accepted': True},
                                      {'id': 'two', 'accepted': False}]})

    def test_idempotency_is_per_call(self):
        stock = {'A': 4}
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 3},
                          'results': [{'id': 'o', 'accepted': True}]})
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 3},
                          'results': [{'id': 'o', 'accepted': True}]})
        self.assertEqual(stock, {'A': 4})


if __name__ == '__main__':
    unittest.main()
