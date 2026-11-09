import copy
import unittest

from allocation import allocate


class IdempotentOrderIdTests(unittest.TestCase):
    def test_accepted_replay_does_not_consume_stock_again(self):
        result = allocate({'A': 2}, [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
        ])
        self.assertEqual(result, {
            'remaining': {'A': 1},
            'results': [{'id': 'x', 'accepted': True},
                        {'id': 'x', 'accepted': True}],
        })

    def test_rejected_replay_stays_rejected(self):
        result = allocate({'A': 1}, [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
            {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
        ])
        self.assertEqual(result, {
            'remaining': {'A': 0},
            'results': [{'id': 'x', 'accepted': False},
                        {'id': 'y', 'accepted': True},
                        {'id': 'x', 'accepted': False}],
        })

    def test_line_order_and_splitting_do_not_change_identity(self):
        stock = {'A': 3, 'B': 1}
        result = allocate(stock, [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                  {'sku': 'A', 'qty': 1},
                                  {'sku': 'B', 'qty': 1}]},
            {'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                  {'sku': 'A', 'qty': 2}]},
        ])
        self.assertEqual(result, {
            'remaining': {'A': 1, 'B': 0},
            'results': [{'id': 'x', 'accepted': True},
                        {'id': 'x', 'accepted': True}],
        })

    def test_different_combined_quantities_raise_and_preserve_inputs(self):
        stock = {'A': 3}
        orders = [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
        ]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_split_lines_differing_only_in_total_raise(self):
        stock = {'A': 3}
        orders = [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                  {'sku': 'A', 'qty': 1}]},
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
        ]
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual(stock, {'A': 3})

    def test_empty_order_follows_identity_rule(self):
        self.assertEqual(allocate({'A': 1}, [
            {'id': 'e', 'items': []},
            {'id': 'e', 'items': []},
        ]), {
            'remaining': {'A': 1},
            'results': [{'id': 'e', 'accepted': True},
                        {'id': 'e', 'accepted': True}],
        })

    def test_empty_order_reused_with_items_raises(self):
        stock = {'A': 1}
        orders = [
            {'id': 'e', 'items': []},
            {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]},
        ]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_same_id_is_allowed_again_in_a_new_call(self):
        self.assertEqual(allocate({'A': 1}, [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
        ])['remaining'], {'A': 0})
        self.assertEqual(allocate({'A': 1}, [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
        ])['remaining'], {'A': 0})


if __name__ == '__main__':
    unittest.main()
