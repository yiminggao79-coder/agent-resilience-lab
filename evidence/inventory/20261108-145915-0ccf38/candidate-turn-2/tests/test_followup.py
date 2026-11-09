import copy
import unittest

from allocation import allocate


class IdempotentIdTests(unittest.TestCase):
    def test_replay_does_not_consume_stock_again(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': {'A': 2},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'x', 'accepted': True}]})

    def test_line_order_and_splitting_keep_identity(self):
        stock = {'A': 4, 'B': 4}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2},
                                        {'sku': 'B', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]}]
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': {'A': 2, 'B': 3},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'x', 'accepted': True}]})

    def test_rejected_id_stays_rejected_even_if_stock_would_allow(self):
        stock = {'A': 2}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'big', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'big', 'items': [{'sku': 'A', 'qty': 3}]}]
        result = allocate(stock, orders)
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True},
                                             {'id': 'big', 'accepted': False},
                                             {'id': 'big', 'accepted': False}])
        self.assertEqual(result['remaining'], {'A': 0})

    def test_replay_of_unknown_sku_stays_rejected(self):
        orders = [{'id': 'u', 'items': [{'sku': 'missing', 'qty': 1}]},
                  {'id': 'u', 'items': [{'sku': 'missing', 'qty': 1}]}]
        self.assertEqual(allocate({'A': 1}, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'u', 'accepted': False},
                                      {'id': 'u', 'accepted': False}]})

    def test_conflicting_quantities_raise_and_inputs_unchanged(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_conflict_detected_even_when_same_sku_set(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        with self.assertRaises(ValueError):
            allocate({'A': 5, 'B': 5}, orders)

    def test_empty_order_replays(self):
        orders = [{'id': 'e', 'items': []}, {'id': 'e', 'items': []}]
        self.assertEqual(allocate({'A': 1}, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'e', 'accepted': True},
                                      {'id': 'e', 'accepted': True}]})

    def test_empty_order_conflicts_with_nonempty_same_id(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}]
        with self.assertRaises(ValueError):
            allocate({'A': 1}, orders)

    def test_distinct_ids_are_independent(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 3}, orders)
        self.assertEqual(result, {'remaining': {'A': 1},
                                  'results': [{'id': 'x', 'accepted': True},
                                              {'id': 'y', 'accepted': False}]})

    def test_case_sensitive_ids_are_distinct(self):
        orders = [{'id': 'X', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        result = allocate({'A': 2}, orders)
        self.assertEqual(result['results'], [{'id': 'X', 'accepted': True},
                                             {'id': 'x', 'accepted': True}])
        self.assertEqual(result['remaining'], {'A': 0})


if __name__ == '__main__':
    unittest.main()
