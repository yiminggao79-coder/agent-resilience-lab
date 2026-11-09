import copy
import unittest

from allocation import allocate


class ExtraAllocationTests(unittest.TestCase):
    def test_case_sensitive_skus_and_zero_keys_preserved(self):
        stock = {'A': 1, 'a': 0, 'zero': 0}
        orders = [{'id': 'lower', 'items': [{'sku': 'a', 'qty': 1}]}]
        result = allocate(stock, orders)
        self.assertEqual(result, {'remaining': {'A': 1, 'a': 0, 'zero': 0},
                                  'results': [{'id': 'lower', 'accepted': False}]})

    def test_no_mutation_across_multiple_orders(self):
        stock = {'A': 5, 'B': 0}
        orders = [{'id': 'one', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'two', 'items': [{'sku': 'A', 'qty': 4}]},
                  {'id': 'three', 'items': [{'sku': 'B', 'qty': 1}]},
                  {'id': 'four', 'items': [{'sku': 'A', 'qty': 3}]}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual(result['results'], [{'id': 'one', 'accepted': True},
                                             {'id': 'two', 'accepted': False},
                                             {'id': 'three', 'accepted': False},
                                             {'id': 'four', 'accepted': True}])
        self.assertEqual(result['remaining'], {'A': 0, 'B': 0})
        self.assertEqual((stock, orders), before)

    def test_combined_lines_sum_before_judging(self):
        stock = {'A': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2},
                                        {'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate(stock, orders),
                         {'remaining': {'A': 0}, 'results': [{'id': 'x', 'accepted': True}]})

    def test_result_rows_have_exact_shape(self):
        result = allocate({'A': 1}, [{'id': 'x', 'items': [], 'note': 'ignored'}])
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True}])

    def test_empty_inputs(self):
        self.assertEqual(allocate({}, []), {'remaining': {}, 'results': []})


if __name__ == '__main__':
    unittest.main()
