import copy
import unittest

from allocation import allocate


class AllocationExtraTests(unittest.TestCase):
    def test_case_sensitive_skus(self):
        stock = {'A': 1, 'a': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'a', 'qty': 2}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 1, 'a': 3},
                          'results': [{'id': 'x', 'accepted': True}]})

    def test_arrival_order_and_zero_keys_preserved(self):
        stock = {'zero': 0, 'A': 2}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 1}]}]
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': {'zero': 0, 'A': 0},
                                  'results': [{'id': 'a', 'accepted': True},
                                              {'id': 'b', 'accepted': False}]})
        self.assertEqual(stock, {'zero': 0, 'A': 2})

    def test_combined_lines_can_be_accepted(self):
        stock = {'A': 4}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 3}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 0},
                          'results': [{'id': 'x', 'accepted': True}]})

    def test_unknown_sku_does_not_consume_known_stock(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'Z', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 5},
                          'results': [{'id': 'x', 'accepted': False}]})

    def test_no_orders_and_inputs_untouched(self):
        stock = {'A': 1}
        before = copy.deepcopy(stock)
        self.assertEqual(allocate(stock, []), {'remaining': {'A': 1}, 'results': []})
        self.assertEqual(stock, before)

    def test_remaining_is_detached_from_stock(self):
        stock = {'A': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate(stock, orders)
        self.assertEqual(result['remaining'], {'A': 1})
        result['remaining']['A'] = 99
        self.assertEqual(stock, {'A': 3})


if __name__ == '__main__':
    unittest.main()
