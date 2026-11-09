import copy
import unittest

from allocation import allocate


class AllocationExtraTests(unittest.TestCase):
    def test_repeated_lines_combined_and_accepted(self):
        result = allocate({'A': 5}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'A', 'qty': 2}]}])
        self.assertEqual(result, {'remaining': {'A': 1}, 'results': [{'id': 'x', 'accepted': True}]})

    def test_case_sensitive_skus(self):
        result = allocate({'A': 1, 'a': 1}, [{'id': 'x', 'items': [{'sku': 'a', 'qty': 1}]}])
        self.assertEqual(result, {'remaining': {'A': 1, 'a': 0}, 'results': [{'id': 'x', 'accepted': True}]})

    def test_zero_stock_key_preserved(self):
        result = allocate({'A': 1, 'zero': 0}, [])
        self.assertEqual(result, {'remaining': {'A': 1, 'zero': 0}, 'results': []})

    def test_rejection_consumes_no_stock(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 5}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]}]
        result = allocate({'A': 1, 'B': 1}, orders)
        self.assertEqual(result, {'remaining': {'A': 0, 'B': 1},
                                  'results': [{'id': 'x', 'accepted': False},
                                              {'id': 'y', 'accepted': True}]})

    def test_no_mutation_of_inputs(self):
        stock = {'A': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual((stock, orders), before)
        result['remaining']['A'] = 99
        self.assertEqual(stock, {'A': 3})

    def test_sequential_orders_arrival_order(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'c', 'items': [{'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate({'A': 3}, orders),
                         {'remaining': {'A': 0}, 'results': [{'id': 'a', 'accepted': True},
                                                              {'id': 'b', 'accepted': False},
                                                              {'id': 'c', 'accepted': True}]})


if __name__ == '__main__':
    unittest.main()
