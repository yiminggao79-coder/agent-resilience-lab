import copy
import unittest

from allocation import allocate


class ExtraAllocationTests(unittest.TestCase):
    def test_case_sensitive_skus_and_zero_keys(self):
        stock = {'sku': 1, 'SKU': 0, 'a': 5}
        orders = [{'id': 's', 'items': [{'sku': 'SKU', 'qty': 1}]},
                  {'id': 'l', 'items': [{'sku': 'sku', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'sku': 0, 'SKU': 0, 'a': 5},
                          'results': [{'id': 's', 'accepted': False},
                                      {'id': 'l', 'accepted': True}]})

    def test_arrival_order_and_result_rows_shape(self):
        orders = [{'id': '1', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': '2', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 3}, orders)
        self.assertEqual([row['id'] for row in result['results']], ['1', '2'])
        for row in result['results']:
            self.assertEqual(set(row), {'id', 'accepted'})
            self.assertIsInstance(row['accepted'], bool)
        self.assertEqual(result['results'], [{'id': '1', 'accepted': True},
                                             {'id': '2', 'accepted': False}])
        self.assertEqual(result['remaining'], {'A': 1})

    def test_rejection_with_unknown_sku_after_known_deducts_nothing(self):
        stock = {'A': 5}
        self.assertEqual(allocate(stock, [{'id': 'x', 'items': [
            {'sku': 'A', 'qty': 3}, {'sku': 'ghost', 'qty': 1}]}]),
            {'remaining': {'A': 5}, 'results': [{'id': 'x', 'accepted': False}]})

    def test_combined_exact_boundary(self):
        stock = {'A': 4}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 3}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 0},
                          'results': [{'id': 'x', 'accepted': True}]})

    def test_inputs_not_mutated_and_output_detached(self):
        stock = {'A': 2, 'B': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'y', 'items': []}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual((stock, orders), before)
        result['remaining']['A'] = 99
        self.assertEqual(stock, before[0])


if __name__ == '__main__':
    unittest.main()
