import copy
import unittest

from allocation import allocate


class ExtraAllocationTests(unittest.TestCase):
    def test_case_sensitive_skus_and_zero_stock_preserved(self):
        stock = {'A': 1, 'a': 2, 'Zero': 0}
        orders = [{'id': 'o1', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'o2', 'items': [{'sku': 'a', 'qty': 2}]},
                  {'id': 'o3', 'items': [{'sku': 'Zero', 'qty': 1}]},
                  {'id': 'o4', 'items': [{'sku': 'A', 'qty': 1}]}]
        self.assertEqual(
            allocate(stock, orders),
            {'remaining': {'A': 0, 'a': 0, 'Zero': 0},
             'results': [{'id': 'o1', 'accepted': True},
                         {'id': 'o2', 'accepted': True},
                         {'id': 'o3', 'accepted': False},
                         {'id': 'o4', 'accepted': False}]},
        )
        self.assertEqual(stock, {'A': 1, 'a': 2, 'Zero': 0})

    def test_remaining_is_detached_from_caller_stock(self):
        stock = {'A': 2}
        result = allocate(stock, [])
        result['remaining']['A'] = 99
        self.assertEqual(stock, {'A': 2})
        self.assertEqual(result['results'], [])

    def test_rows_are_exact(self):
        result = allocate({'A': 0}, [{'id': 'x', 'items': []}])
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True}])

    def test_repeated_lines_combined_then_accepted(self):
        stock = {'A': 4}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2},
                                        {'sku': 'A', 'qty': 2},
                                        {'sku': 'A', 'qty': 1}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        self.assertEqual(
            allocate(stock, orders),
            {'remaining': {'A': 3},
             'results': [{'id': 'x', 'accepted': False},
                         {'id': 'y', 'accepted': True}]},
        )
        self.assertEqual((stock, orders), before)


if __name__ == '__main__':
    unittest.main()
