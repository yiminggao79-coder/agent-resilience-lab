import copy
import unittest

from allocation import allocate


class IdempotentOrderIdTests(unittest.TestCase):
    def test_replay_returns_original_result_without_consuming_stock(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 5}, orders)
        self.assertEqual(result, {'remaining': {'A': 3},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'x', 'accepted': True}]})

    def test_replay_identity_ignores_line_order(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 2}, {'sku': 'A', 'qty': 1}]}]
        result = allocate({'A': 1, 'B': 2}, orders)
        self.assertEqual(result, {'remaining': {'A': 0, 'B': 0},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'x', 'accepted': True}]})

    def test_replay_identity_ignores_line_splitting(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'B', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]}]
        result = allocate({'A': 2, 'B': 1}, orders)
        self.assertEqual(result, {'remaining': {'A': 0, 'B': 0},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'x', 'accepted': True}]})

    def test_rejected_id_stays_rejected_on_later_replay(self):
        # First attempt fails; stock is still untouched, but the ID is pinned
        # to its original rejection even though stock exists now.
        orders = [{'id': 'blocker', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 2}, orders)
        self.assertEqual(result, {'remaining': {'A': 0},
                                  'results': [{'id': 'blocker', 'accepted': True},
                                              {'id': 'x', 'accepted': False},
                                              {'id': 'x', 'accepted': False}]})

    def test_rejected_replay_with_insufficient_stock_at_first(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 5}]}]
        result = allocate({'A': 3}, orders)
        self.assertEqual(result, {'remaining': {'A': 3},
                                  'results': [{'id': 'x', 'accepted': False},
                                              {'id': 'x', 'accepted': False}]})

    def test_reused_id_with_different_quantities_raises(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        stock = {'A': 5}
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_reused_id_with_different_skus_raises_and_inputs_unchanged(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'ok', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1}]}]
        stock = {'A': 2, 'B': 1}
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_conflict_after_replay_still_raises(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        with self.assertRaises(ValueError):
            allocate({'A': 5}, orders)

    def test_empty_order_identity(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': []}]
        result = allocate({'A': 0}, orders)
        self.assertEqual(result, {'remaining': {'A': 0},
                                  'results': [{'id': 'e', 'accepted': True},
                                              {'id': 'e', 'accepted': True}]})

    def test_empty_order_reused_with_items_raises(self):
        orders = [{'id': 'x', 'items': []},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        with self.assertRaises(ValueError):
            allocate({'A': 1}, orders)

    def test_zero_qty_lines_cancel_in_identity(self):
        # Two lines summing to zero combine to the same empty identity.
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 2}, orders)
        self.assertEqual(result, {'remaining': {'A': 0},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'x', 'accepted': True}]})

    def test_distinct_ids_unaffected(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 3}, orders)
        self.assertEqual(result, {'remaining': {'A': 1},
                                  'results': [{'id': 'a', 'accepted': True},
                                              {'id': 'b', 'accepted': False}]})

    def test_successful_replay_does_not_mutate_inputs(self):
        stock = {'A': 4, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual((stock, orders), before)
        result['remaining']['A'] = 99
        self.assertEqual(stock, {'A': 4, 'zero': 0})


if __name__ == '__main__':
    unittest.main()
