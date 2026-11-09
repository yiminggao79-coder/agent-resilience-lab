import copy
import unittest

from allocation import allocate


class AllocationRegressionTests(unittest.TestCase):
    def test_failed_order_leaves_stock_for_later_orders(self):
        orders = [{'id': 'first', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'B', 'qty': 5}]},
                  {'id': 'second', 'items': [{'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate({'A': 2}, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'first', 'accepted': False},
                                      {'id': 'second', 'accepted': True}]})

    def test_repeated_sku_lines_are_summed(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2},
                                        {'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 4, 'B': 1}, orders),
                         {'remaining': {'A': 0, 'B': 0},
                          'results': [{'id': 'x', 'accepted': True}]})
        self.assertEqual(allocate({'A': 3, 'B': 1}, orders),
                         {'remaining': {'A': 3, 'B': 1},
                          'results': [{'id': 'x', 'accepted': False}]})

    def test_caller_inputs_not_mutated_and_result_detached(self):
        stock = {'A': 2, 'zero': 0, 'Zero': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        result = allocate(stock, orders)
        self.assertEqual((stock, orders), before)
        result['remaining']['A'] = 999
        self.assertEqual((stock, orders), before)

    def test_case_sensitive_skus_and_zero_stock_preserved(self):
        orders = [{'id': 'lower', 'items': [{'sku': 'abc', 'qty': 1}]},
                  {'id': 'upper', 'items': [{'sku': 'ABC', 'qty': 1}]}]
        self.assertEqual(allocate({'ABC': 1, 'abc': 0}, orders),
                         {'remaining': {'ABC': 0, 'abc': 0},
                          'results': [{'id': 'lower', 'accepted': False},
                                      {'id': 'upper', 'accepted': True}]})

    def test_unknown_sku_alone_rejects_empty_order_accepted(self):
        orders = [{'id': 'u', 'items': [{'sku': 'nope', 'qty': 3}]},
                  {'id': 'e', 'items': []}]
        self.assertEqual(allocate({}, orders),
                         {'remaining': {},
                          'results': [{'id': 'u', 'accepted': False},
                                      {'id': 'e', 'accepted': True}]})

    def test_row_shape_and_order_sequence(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'c', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 3}, orders)
        self.assertEqual([list(row) for row in result['results']],
                         [['id', 'accepted']] * 3)
        self.assertEqual([row['id'] for row in result['results']], ['a', 'b', 'c'])
        self.assertEqual([row['accepted'] for row in result['results']],
                         [True, False, True])
        self.assertEqual(result['remaining'], {'A': 0})


if __name__ == '__main__':
    unittest.main()
