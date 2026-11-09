import copy
import unittest

from allocation import allocate


class ExtraAllocationTests(unittest.TestCase):
    def test_case_sensitivity_and_zero_keys(self):
        stock = {'A': 1, 'a': 2, 'Z': 0}
        result = allocate(stock, [{'id': '1', 'items': [{'sku': 'A', 'qty': 1}]},
                                  {'id': '2', 'items': [{'sku': 'a', 'qty': 2}]},
                                  {'id': '3', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(result['remaining'], {'A': 0, 'a': 0, 'Z': 0})
        self.assertEqual(result['results'], [
            {'id': '1', 'accepted': True},
            {'id': '2', 'accepted': True},
            {'id': '3', 'accepted': False},
        ])
        self.assertEqual(stock, {'A': 1, 'a': 2, 'Z': 0})

    def test_arrival_order_and_no_phantom_sku(self):
        stock = {'A': 1}
        result = allocate(stock, [{'id': '1', 'items': [{'sku': 'A', 'qty': 1}]},
                                  {'id': '2', 'items': [{'sku': 'A', 'qty': 1}]},
                                  {'id': '3', 'items': [{'sku': 'B', 'qty': 1}]}])
        self.assertEqual(result['remaining'], {'A': 0})
        self.assertEqual([r['accepted'] for r in result['results']], [True, False, False])

    def test_rejection_consumes_nothing(self):
        stock = {'A': 5, 'B': 0}
        before = copy.deepcopy(stock)
        result = allocate(stock, [{'id': '1', 'items': [{'sku': 'A', 'qty': 3},
                                                        {'sku': 'B', 'qty': 1}]}])
        self.assertEqual(result['remaining'], before)
        self.assertEqual(result['results'], [{'id': '1', 'accepted': False}])
        self.assertEqual(stock, before)

    def test_result_rows_are_exact(self):
        result = allocate({'A': 1}, [{'id': '1', 'items': []}])
        self.assertEqual(result['results'], [{'id': '1', 'accepted': True}])
        self.assertEqual(set(result.keys()), {'remaining', 'results'})


if __name__ == '__main__':
    unittest.main()
