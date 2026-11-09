import copy
import unittest

from allocation import allocate


def snapshot(stock, orders):
    return copy.deepcopy((stock, orders))


class IdempotentOrderTests(unittest.TestCase):
    def test_replay_does_not_consume_stock_again(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = snapshot(stock, orders)
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 3},
                          'results': [{'id': 'x', 'accepted': True},
                                      {'id': 'x', 'accepted': True}]})
        self.assertEqual((stock, orders), before)

    def test_replay_identity_ignores_line_order_and_splitting(self):
        stock = {'A': 4, 'B': 4}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2},
                                        {'sku': 'B', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 2, 'B': 3},
                          'results': [{'id': 'x', 'accepted': True},
                                      {'id': 'x', 'accepted': True}]})

    def test_rejected_replay_stays_rejected(self):
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'x', 'accepted': False},
                                      {'id': 'x', 'accepted': False}]})

    def test_rejected_stays_rejected_even_if_stock_would_allow(self):
        stock = {'A': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'unknown', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'unknown', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 3},
                          'results': [{'id': 'x', 'accepted': False},
                                      {'id': 'x', 'accepted': False}]})

    def test_same_id_different_quantities_raises(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = snapshot(stock, orders)
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_conflicting_replay_of_rejected_id_raises(self):
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        before = snapshot(stock, orders)
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_empty_orders_share_identity(self):
        stock = {'A': 1}
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': []}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'e', 'accepted': True},
                                      {'id': 'e', 'accepted': True}]})
        with self.assertRaises(ValueError):
            allocate(stock, [{'id': 'e', 'items': []},
                             {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])

    def test_ids_are_scoped_per_call(self):
        stock = {'A': 2}
        first = allocate(stock, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertTrue(first['results'][0]['accepted'])
        second = allocate(stock, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(second, {'remaining': {'A': 1},
                                  'results': [{'id': 'x', 'accepted': True}]})

    def test_distinct_ids_still_consume_successively(self):
        stock = {'A': 3}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'c', 'items': [{'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 0},
                          'results': [{'id': 'a', 'accepted': True},
                                      {'id': 'b', 'accepted': False},
                                      {'id': 'c', 'accepted': True}]})


if __name__ == '__main__':
    unittest.main()
