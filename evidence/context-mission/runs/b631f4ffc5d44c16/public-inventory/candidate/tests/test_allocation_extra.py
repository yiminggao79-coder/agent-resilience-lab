import copy
import unittest

from allocation import allocate


class AllocationExtraTests(unittest.TestCase):
    def test_case_sensitive_and_zero_keys_preserved(self):
        stock = {'A': 2, 'a': 1, 'zero': 0}
        result = allocate(stock, [{'id': 'x', 'items': [{'sku': 'a', 'qty': 1}]},
                                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(result['remaining'], {'A': 0, 'a': 0, 'zero': 0})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True},
                                             {'id': 'y', 'accepted': True}])

    def test_rejection_consumes_no_stock(self):
        stock = {'A': 5, 'B': 0}
        result = allocate(stock, [{'id': 'r', 'items': [{'sku': 'A', 'qty': 1},
                                                        {'sku': 'B', 'qty': 1}]},
                                  {'id': 'ok', 'items': [{'sku': 'A', 'qty': 4}]}])
        self.assertEqual(result['remaining'], {'A': 1, 'B': 0})
        self.assertEqual(result['results'], [{'id': 'r', 'accepted': False},
                                             {'id': 'ok', 'accepted': True}])

    def test_order_and_inputs_not_mutated(self):
        stock = {'A': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual((stock, orders), before)
        self.assertIsNot(result['remaining'], stock)
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True}])

    def test_empty_inputs(self):
        self.assertEqual(allocate({}, []), {'remaining': {}, 'results': []})
        self.assertEqual(allocate({}, [{'id': 'e', 'items': []}]),
                         {'remaining': {}, 'results': [{'id': 'e', 'accepted': True}]})

    def test_rows_exact_shape(self):
        result = allocate({'A': 1}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(set(result), {'remaining', 'results'})
        self.assertEqual(result['results'][0], {'id': 'x', 'accepted': True})
        self.assertEqual(set(result['results'][0]), {'id', 'accepted'})


if __name__ == '__main__':
    unittest.main()
