import copy
import unittest

from allocation import allocate


class IdempotentIdTests(unittest.TestCase):
    def test_replay_same_id_does_not_consume_again(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate(stock, orders)
        self.assertEqual(result['remaining'], {'A': 3})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True},
                                             {'id': 'x', 'accepted': True}])

    def test_replay_preserves_rejected_result(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 10}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 10}]}]
        result = allocate({'A': 1}, orders)
        self.assertEqual(result['remaining'], {'A': 1})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': False},
                                             {'id': 'x', 'accepted': False}])

    def test_replay_identity_ignores_line_order_and_split(self):
        stock = {'A': 10, 'B': 10}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'B', 'qty': 3}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1}, {'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 2}, {'sku': 'A', 'qty': 1}]}]
        result = allocate(stock, orders)
        self.assertEqual(result['remaining'], {'A': 8, 'B': 7})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True},
                                             {'id': 'x', 'accepted': True}])

    def test_same_id_different_quantities_raises(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_raise_on_different_quantities_does_not_mutate_inputs(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_empty_order_identity_rule(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': []}]
        result = allocate({'A': 1}, orders)
        self.assertEqual(result['remaining'], {'A': 1})
        self.assertEqual(result['results'], [{'id': 'e', 'accepted': True},
                                             {'id': 'e', 'accepted': True}])
        with self.assertRaises(ValueError):
            allocate({'A': 1}, [{'id': 'e', 'items': []},
                                {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])

    def test_same_quantities_after_rejection_replays_rejection(self):
        orders = [{'id': 'x', 'items': [{'sku': 'missing', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'missing', 'qty': 1}]}]
        before = copy.deepcopy(orders)
        result = allocate({'A': 1}, orders)
        self.assertEqual(orders, before)
        self.assertEqual(result['remaining'], {'A': 1})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': False},
                                             {'id': 'x', 'accepted': False}])


if __name__ == '__main__':
    unittest.main()
