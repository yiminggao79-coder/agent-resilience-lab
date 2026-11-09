import copy
import unittest

from allocation import allocate


class ExtraAllocationTests(unittest.TestCase):
    def test_case_sensitive_skus(self):
        stock = {'A': 1, 'a': 5}
        orders = [{'id': '1', 'items': [{'sku': 'a', 'qty': 5}]},
                  {'id': '2', 'items': [{'sku': 'A', 'qty': 1}]}]
        result = allocate(stock, orders)
        self.assertEqual(result['remaining'], {'A': 0, 'a': 0})
        self.assertEqual(result['results'], [{'id': '1', 'accepted': True},
                                             {'id': '2', 'accepted': True}])

    def test_combined_lines_checked_together(self):
        stock = {'A': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'A', 'qty': 2}]},
                  {'id': 'z', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate(stock, orders), {
            'remaining': {'A': 0},
            'results': [{'id': 'x', 'accepted': True},
                        {'id': 'y', 'accepted': False},
                        {'id': 'z', 'accepted': True}],
        })

    def test_no_mutation_and_rows_shape(self):
        stock = {'A': 2, 'B': 0, 'c': 0}
        orders = [{'id': 'o1', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'o2', 'items': []}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual((stock, orders), before)
        self.assertEqual(result['remaining'], {'A': 1, 'B': 0, 'c': 0})
        self.assertIsNot(result['remaining'], stock)
        for row in result['results']:
            self.assertEqual(set(row), {'id', 'accepted'})
            self.assertIsInstance(row['accepted'], bool)
        self.assertEqual(result['results'], [{'id': 'o1', 'accepted': True},
                                             {'id': 'o2', 'accepted': True}])

    def test_all_rejected_preserves_stock(self):
        stock = {'A': 0}
        self.assertEqual(allocate(stock, [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]}]),
                         {'remaining': {'A': 0}, 'results': [{'id': 'a', 'accepted': False}]})

    def test_zero_quantity_stock_key_added_only_on_accept(self):
        self.assertEqual(allocate({}, [{'id': 'e', 'items': []}]), {'remaining': {}, 'results': [{'id': 'e', 'accepted': True}]})


if __name__ == '__main__':
    unittest.main()
