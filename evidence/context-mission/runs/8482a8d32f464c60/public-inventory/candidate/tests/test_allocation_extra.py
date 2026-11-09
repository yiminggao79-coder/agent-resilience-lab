import copy
import unittest

from allocation import allocate


class ExtraAllocationTests(unittest.TestCase):
    def test_duplicate_skus_are_summed(self):
        result = allocate({'A': 4}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2},
                                                             {'sku': 'A', 'qty': 2}]}])
        self.assertEqual(result, {'remaining': {'A': 0},
                                  'results': [{'id': 'x', 'accepted': True}]})

    def test_rejection_consumes_nothing_mid_order(self):
        stock = {'A': 5, 'B': 0}
        result = allocate(stock, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3},
                                                         {'sku': 'B', 'qty': 1}]}])
        self.assertEqual(result['remaining'], {'A': 5, 'B': 0})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': False}])

    def test_empty_order_and_stock_untouched(self):
        stock = {'A': 1, 'zero': 0}
        orders = [{'id': 'e', 'items': []}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': {'A': 1, 'zero': 0},
                                  'results': [{'id': 'e', 'accepted': True}]})
        self.assertEqual((stock, orders), before)
        result['remaining']['A'] = 99
        self.assertEqual(stock, before[0])

    def test_case_sensitive_and_unknown_sku(self):
        result = allocate({'a': 2}, [{'id': 'upper', 'items': [{'sku': 'A', 'qty': 1}]},
                                     {'id': 'lower', 'items': [{'sku': 'a', 'qty': 2}]}])
        self.assertEqual(result, {'remaining': {'a': 0},
                                  'results': [{'id': 'upper', 'accepted': False},
                                              {'id': 'lower', 'accepted': True}]})

    def test_result_rows_are_exact(self):
        result = allocate({'A': 1}, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(set(result), {'remaining', 'results'})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True}])


if __name__ == '__main__':
    unittest.main()
