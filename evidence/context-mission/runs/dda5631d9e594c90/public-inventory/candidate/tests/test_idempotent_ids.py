import copy
import unittest

from allocation import allocate


class IdempotentIdTests(unittest.TestCase):
    def test_replay_does_not_consume_stock_again(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 3}, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'a', 'accepted': True},
                                      {'id': 'a', 'accepted': True}]})

    def test_split_and_reordered_lines_have_same_identity(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 2}]},
                  {'id': 'a', 'items': [{'sku': 'B', 'qty': 1}, {'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1}]}]
        self.assertEqual(allocate({'A': 1, 'B': 2}, orders),
                         {'remaining': {'A': 0, 'B': 0},
                          'results': [{'id': 'a', 'accepted': True},
                                      {'id': 'a', 'accepted': True}]})

    def test_rejected_id_stays_rejected_on_later_replay(self):
        # Stock is 3: 'a' needs 5, so it is unaffordable when first seen and
        # 'b' consumes 1. The replay of 'a' (same 5, split into two lines)
        # would fit in the leftover 2 only if it were rescued, but identity
        # requires it to stay rejected.
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'a', 'items': [{'sku': 'A', 'qty': 3}, {'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 3}, orders),
                         {'remaining': {'A': 2},
                          'results': [{'id': 'a', 'accepted': False},
                                      {'id': 'b', 'accepted': True},
                                      {'id': 'a', 'accepted': False}]})

    def test_accepted_replay_keeps_original_outcome_after_stock_depleted(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(allocate({'A': 4}, orders),
                         {'remaining': {'A': 0},
                          'results': [{'id': 'a', 'accepted': True},
                                      {'id': 'b', 'accepted': True},
                                      {'id': 'a', 'accepted': True}]})

    def test_conflicting_quantities_raise_and_inputs_unchanged(self):
        stock = {'A': 5}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_conflict_detected_even_after_stock_would_run_out(self):
        stock = {'A': 1}
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'a', 'items': [{'sku': 'nope', 'qty': 1}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_different_skus_same_id_is_a_conflict(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'a', 'items': [{'sku': 'b', 'qty': 1}]}]
        with self.assertRaises(ValueError):
            allocate({'A': 1, 'b': 1}, orders)

    def test_empty_orders_follow_identity_rule(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': []}]
        self.assertEqual(allocate({'A': 1}, orders),
                         {'remaining': {'A': 1},
                          'results': [{'id': 'e', 'accepted': True},
                                      {'id': 'e', 'accepted': True}]})

    def test_empty_replay_conflicting_with_nonempty_raises(self):
        orders = [{'id': 'e', 'items': []},
                  {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}]
        with self.assertRaises(ValueError):
            allocate({'A': 1}, orders)

    def test_distinct_ids_still_consume_separately(self):
        orders = [{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'b', 'items': [{'sku': 'A', 'qty': 1}]}]
        self.assertEqual(allocate({'A': 1}, orders),
                         {'remaining': {'A': 0},
                          'results': [{'id': 'a', 'accepted': True},
                                      {'id': 'b', 'accepted': False}]})


if __name__ == '__main__':
    unittest.main()
