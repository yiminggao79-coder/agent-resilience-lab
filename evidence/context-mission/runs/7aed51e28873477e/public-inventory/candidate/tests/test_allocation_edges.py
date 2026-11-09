import copy
import unittest

from allocation import allocate


class AllocationEdgeTests(unittest.TestCase):
    def test_case_sensitive_skus(self):
        result = allocate({'A': 1, 'a': 5}, [{'id': 'o', 'items': [{'sku': 'a', 'qty': 4}]}])
        self.assertEqual(result, {'remaining': {'A': 1, 'a': 1},
                                 'results': [{'id': 'o', 'accepted': True}]})

    def test_combined_repeated_lines_rejection_consumes_nothing(self):
        stock = {'A': 1, 'B': 0}
        orders = [{'id': 'combine', 'items': [{'sku': 'B', 'qty': 1},
                                              {'sku': 'A', 'qty': 1},
                                              {'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 1, 'B': 0},
                          'results': [{'id': 'combine', 'accepted': False}]})

    def test_inputs_not_mutated_and_returned_stock_detached(self):
        stock = {'A': 5}
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        result['remaining']['A'] = 99
        self.assertEqual((stock, orders), before)

    def test_multiple_orders_in_arrival_order(self):
        orders = [{'id': 'one', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'two', 'items': []},
                  {'id': 'three', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 3, 'B': 0}, orders),
                         {'remaining': {'A': 1, 'B': 0},
                          'results': [{'id': 'one', 'accepted': True},
                                      {'id': 'two', 'accepted': True},
                                      {'id': 'three', 'accepted': False}]})

    def test_empty_stock_and_orders(self):
        self.assertEqual(allocate({}, []), {'remaining': {}, 'results': []})

    def test_results_rows_have_exact_keys(self):
        result = allocate({'A': 1}, [{'id': 'o', 'items': []}])
        self.assertEqual(set(result['results'][0]), {'id', 'accepted'})
        self.assertIs(result['results'][0]['accepted'], True)


if __name__ == '__main__':
    unittest.main()
