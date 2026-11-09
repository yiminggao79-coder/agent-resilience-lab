import copy
import unittest

from allocation import allocate


class AllocationExtraTests(unittest.TestCase):
    def test_zero_stock_key_preserved_and_case_sensitive(self):
        result = allocate({'a': 0, 'A': 5},
                          [{'id': 'one', 'items': [{'sku': 'A', 'qty': 5}]},
                           {'id': 'two', 'items': [{'sku': 'a', 'qty': 1}]}])
        self.assertEqual(result, {'remaining': {'a': 0, 'A': 0},
                                  'results': [{'id': 'one', 'accepted': True},
                                              {'id': 'two', 'accepted': False}]})

    def test_duplicate_lines_combined_accept(self):
        stock = {'A': 4}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 3}]}]
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': {'A': 0},
                                  'results': [{'id': 'x', 'accepted': True}]})
        self.assertEqual(stock, {'A': 4})

    def test_sequential_stock_consumption(self):
        result = allocate({'A': 2},
                          [{'id': 'one', 'items': [{'sku': 'A', 'qty': 2}]},
                           {'id': 'two', 'items': [{'sku': 'A', 'qty': 1}]},
                           {'id': 'three', 'items': []}])
        self.assertEqual(result['remaining'], {'A': 0})
        self.assertEqual([row['accepted'] for row in result['results']],
                         [True, False, True])

    def test_no_mutation_and_row_shape(self):
        stock = {'A': 5, 'B': 0}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2},
                                        {'sku': 'Z', 'qty': 1}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 3}]}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual((stock, orders), before)
        self.assertIsNot(result['remaining'], stock)
        self.assertNotEqual(result['remaining'], stock)
        for row in result['results']:
            self.assertEqual(set(row), {'id', 'accepted'})
            self.assertIsInstance(row['accepted'], bool)

    def test_empty_inputs(self):
        self.assertEqual(allocate({}, []), {'remaining': {}, 'results': []})


if __name__ == '__main__':
    unittest.main()
