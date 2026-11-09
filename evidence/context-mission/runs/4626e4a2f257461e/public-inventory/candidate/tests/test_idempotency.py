import copy
import unittest

from allocation import allocate


class IdempotencyTests(unittest.TestCase):
    def test_same_id_same_quantities_replays_without_consuming(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 5}, orders),
                         {'remaining': {'A': 3},
                          'results': [{'id': 'x', 'accepted': True},
                                      {'id': 'x', 'accepted': True}]})

    def test_line_order_and_splitting_do_not_change_identity(self):
        # Both orders combine to {A: 2, B: 1}; only line order/splitting differ.
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2},
                                        {'sku': 'B', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate({'A': 4, 'B': 3}, orders),
                         {'remaining': {'A': 2, 'B': 2},
                          'results': [{'id': 'x', 'accepted': True},
                                      {'id': 'x', 'accepted': True}]})

    def test_split_lines_replay_identical(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate({'A': 2}, orders),
                         {'remaining': {'A': 0},
                          'results': [{'id': 'x', 'accepted': True},
                                      {'id': 'x', 'accepted': True}]})

    def test_previously_rejected_id_stays_rejected_on_replay(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 5}]}]
        result = allocate({'A': 1}, orders)
        self.assertEqual(result['results'],
                         [{'id': 'x', 'accepted': False},
                          {'id': 'y', 'accepted': True},
                          {'id': 'x', 'accepted': False}])
        self.assertEqual(result['remaining'], {'A': 0})

    def test_empty_order_id_identity(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': []}]
        self.assertEqual(allocate({'A': 1}, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'e', 'accepted': True},
                                      {'id': 'e', 'accepted': True}]})

    def test_empty_order_conflicts_with_nonempty_same_id(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}]
        with self.assertRaises(ValueError):
            allocate({'A': 1}, orders)

    def test_conflicting_id_raises_and_inputs_unchanged(self):
        stock = {'A': 5, 'zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_conflict_detected_via_combined_quantities(self):
        # A:2 split vs A:3 combined are different identities.
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        stock = {'A': 9}
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_distinct_ids_unaffected(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate({'A': 1}, orders),
                         {'remaining': {'A': 0},
                          'results': [{'id': 'a', 'accepted': True},
                                      {'id': 'b', 'accepted': False}]})

    def test_idempotency_is_scoped_to_single_call(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}]
        first = allocate({'A': 1}, orders)
        second = allocate({'A': 1}, orders)
        self.assertEqual(first, second)


if __name__ == '__main__':
    unittest.main()
