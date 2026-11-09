import copy
import unittest

from allocation import allocate


class AllocationExtraTests(unittest.TestCase):
    def test_empty_inputs(self):
        self.assertEqual(allocate({}, []), {'remaining': {}, 'results': []})

    def test_zero_stock_key_and_case_sensitivity(self):
        stock = {'A': 0, 'a': 2}
        orders = [{'id': 'o1', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'o2', 'items': [{'sku': 'a', 'qty': 2}]}]
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': {'A': 0, 'a': 0},
                                  'results': [{'id': 'o1', 'accepted': False},
                                              {'id': 'o2', 'accepted': True}]})

    def test_arrival_order_and_sequential_consumption(self):
        stock = {'S': 5}
        orders = [{'id': 'a', 'items': [{'sku': 'S', 'qty': 3}]},
                  {'id': 'b', 'items': [{'sku': 'S', 'qty': 3}]},
                  {'id': 'c', 'items': [{'sku': 'S', 'qty': 2}]}]
        result = allocate(stock, orders)
        self.assertEqual(result['remaining'], {'S': 0})
        self.assertEqual(result['results'], [{'id': 'a', 'accepted': True},
                                             {'id': 'b', 'accepted': False},
                                             {'id': 'c', 'accepted': True}])

    def test_combined_lines_can_deduct_multiple_skus(self):
        stock = {'A': 2, 'B': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]}]
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': {'A': 0, 'B': 2},
                                  'results': [{'id': 'x', 'accepted': True}]})

    def test_rejection_partial_availability_leaves_stock_intact(self):
        stock = {'A': 5, 'B': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1}]}]
        before = copy.deepcopy(stock)
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': before,
                                  'results': [{'id': 'x', 'accepted': False}]})
        self.assertEqual(stock, before)

    def test_inputs_not_mutated_and_remaining_is_independent(self):
        stock = {'A': 4}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual((stock, orders), before)
        result['remaining']['A'] = 999
        result['results'][0]['accepted'] = 'mutated'
        self.assertEqual((stock, orders), before)

    def test_missing_combination_when_only_partial_stock_known(self):
        # SKU present in a rejected order but absent from stock: unavailable.
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'NEW', 'qty': 1},
                                        {'sku': 'NEW', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'x', 'accepted': False}]})

    def test_each_order_processed_independently(self):
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'y', 'items': []},
                  {'id': 'z', 'items': [{'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 0},
                          'results': [{'id': 'x', 'accepted': True},
                                      {'id': 'y', 'accepted': True},
                                      {'id': 'z', 'accepted': False}]})


if __name__ == '__main__':
    unittest.main()
