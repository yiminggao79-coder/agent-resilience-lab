import copy
import unittest

from allocation import allocate


class AllocationTests(unittest.TestCase):
    def test_rejection_is_atomic(self):
        orders = [{'id': 'first', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 2}]},
                  {'id': 'second', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 2, 'B': 1}, orders)
        self.assertEqual(result, {'remaining': {'A': 0, 'B': 1},
                         'results': [{'id': 'first', 'accepted': False}, {'id': 'second', 'accepted': True}]})

    def test_combined_lines_and_inputs_preserved(self):
        stock = {'A': 3, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}, {'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        self.assertEqual(allocate(stock, orders), {'remaining': stock, 'results': [{'id': 'x', 'accepted': False}]})
        self.assertEqual((stock, orders), before)

    def test_unknown_and_empty(self):
        self.assertEqual(allocate({'A': 1}, [{'id': 'x', 'items': [{'sku': 'missing', 'qty': 1}]},
                                            {'id': 'empty', 'items': []}]),
                         {'remaining': {'A': 1}, 'results': [{'id': 'x', 'accepted': False},
                                                            {'id': 'empty', 'accepted': True}]})
