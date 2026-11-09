import copy
import unittest

from allocation import allocate


class IdempotentAllocationTests(unittest.TestCase):
    def test_replay_does_not_consume_again(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 5}, orders)
        self.assertEqual(result, {'remaining': {'A': 3},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'x', 'accepted': True}]})

    def test_line_order_and_splitting_do_not_change_identity(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1}]}]
        result = allocate({'A': 1, 'B': 2}, orders)
        self.assertEqual(result, {'remaining': {'A': 0, 'B': 0},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'x', 'accepted': True}]})

    def test_rejected_replay_stays_rejected(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        result = allocate({'A': 2}, orders)
        self.assertEqual(result, {'remaining': {'A': 2},
                                  'results': [{'id': 'x', 'accepted': False},
                                              {'id': 'x', 'accepted': False}]})

    def test_conflicting_quantities_raise_and_preserve_inputs(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'ok', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_empty_order_identity(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': []}]
        result = allocate({'A': 1}, orders)
        self.assertEqual(result, {'remaining': {'A': 1},
                                  'results': [{'id': 'e', 'accepted': True},
                                              {'id': 'e', 'accepted': True}]})

    def test_empty_and_nonempty_same_id_conflict(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy((orders,))
        with self.assertRaises(ValueError):
            allocate({'A': 1}, orders)
        self.assertEqual((orders,), before)

    def test_distinct_ids_are_independent(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 1}]}]
        result = allocate({'A': 2}, orders)
        self.assertEqual(result, {'remaining': {'A': 0},
                                  'results': [{'id': 'a', 'accepted': True},
                                              {'id': 'b', 'accepted': True}]})


if __name__ == '__main__':
    unittest.main()
