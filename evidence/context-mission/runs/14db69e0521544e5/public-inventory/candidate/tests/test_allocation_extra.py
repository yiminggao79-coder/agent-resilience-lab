import copy
import unittest

from allocation import allocate


class AdditionalAllocationTests(unittest.TestCase):
    def test_duplicate_lines_sum_to_accept(self):
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 3}, orders)
        self.assertEqual(result, {'remaining': {'A': 0},
                                  'results': [{'id': 'o', 'accepted': True}]})

    def test_partial_rejection_consumes_no_stock(self):
        stock = {'A': 5, 'B': 0}
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 1}]}]
        result = allocate(stock, orders)
        self.assertEqual(result['remaining'], {'A': 5, 'B': 0})
        self.assertEqual(result['results'], [{'id': 'o', 'accepted': False}])
        self.assertEqual(stock, {'A': 5, 'B': 0})

    def test_unknown_sku_after_known_sku_is_unavailable(self):
        stock = {'A': 1}
        result = allocate(stock, [{'id': 'o', 'items': [{'sku': 'A', 'qty': 1},
                                                        {'sku': 'missing', 'qty': 1}]}])
        self.assertEqual(result, {'remaining': {'A': 1},
                                  'results': [{'id': 'o', 'accepted': False}]})

    def test_case_sensitive_skus(self):
        result = allocate({'A': 1, 'a': 2},
                          [{'id': 'o', 'items': [{'sku': 'a', 'qty': 2}]}])
        self.assertEqual(result['remaining'], {'A': 1, 'a': 0})
        self.assertTrue(result['results'][0]['accepted'])

    def test_keys_preserved_and_inputs_not_mutated(self):
        stock = {'A': 2, 'zero': 0}
        orders = [{'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual(result['remaining'], {'A': 0, 'zero': 0})
        self.assertEqual((stock, orders), before)
        self.assertIsNot(result['remaining'], stock)

    def test_mutation_of_result_does_not_affect_caller_stock(self):
        stock = {'A': 2}
        result = allocate(stock, [])
        result['remaining']['A'] = 99
        self.assertEqual(stock, {'A': 2})

    def test_arrival_order(self):
        orders = [{'id': '1', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': '2', 'items': [{'sku': 'A', 'qty': 1}]}]
        result = allocate({'A': 2}, orders)
        self.assertEqual(result['results'], [{'id': '1', 'accepted': True},
                                             {'id': '2', 'accepted': False}])
        self.assertEqual(result['remaining'], {'A': 0})


if __name__ == '__main__':
    unittest.main()
