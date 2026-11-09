import copy
import unittest

from allocation import allocate


class IdempotentIdTests(unittest.TestCase):
    def test_same_id_same_quantities_replays_without_consuming(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(
            allocate(stock, orders),
            {'remaining': {'A': 3},
             'results': [{'id': 'x', 'accepted': True},
                         {'id': 'x', 'accepted': True}]},
        )

    def test_line_order_and_splitting_do_not_change_identity(self):
        stock = {'A': 5, 'B': 5}
        orders = [
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 2},
                                  {'sku': 'B', 'qty': 1}]},
            {'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                  {'sku': 'A', 'qty': 1},
                                  {'sku': 'A', 'qty': 1}]},
        ]
        self.assertEqual(
            allocate(stock, orders),
            {'remaining': {'A': 3, 'B': 4},
             'results': [{'id': 'x', 'accepted': True},
                         {'id': 'x', 'accepted': True}]},
        )

    def test_rejected_id_stays_rejected_on_replay(self):
        # Replay must not "re-try" after other orders freed nothing: it
        # replays the original verdict even though stock may have changed.
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 5}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 5}]}]
        self.assertEqual(
            allocate(stock, orders),
            {'remaining': {'A': 0},
             'results': [{'id': 'x', 'accepted': False},
                         {'id': 'y', 'accepted': True},
                         {'id': 'x', 'accepted': False}]},
        )

    def test_rejected_id_replayed_when_stock_would_now_suffice(self):
        stock = {'A': 1}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        self.assertEqual(
            allocate(stock, orders),
            {'remaining': {'A': 1},
             'results': [{'id': 'x', 'accepted': False},
                         {'id': 'x', 'accepted': False}]},
        )

    def test_same_id_different_quantities_raises_without_mutation(self):
        stock = {'A': 5, 'Zero': 0}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_different_skus_or_extra_zero_line_is_different_identity(self):
        stock = {'A': 5, 'B': 5}
        for changed in ([{'sku': 'A', 'qty': 2}, {'sku': 'B', 'qty': 1}],
                        [{'sku': 'A', 'qty': 1}, {'sku': 'B', 'qty': 1}]):
            orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                      {'id': 'x', 'items': changed}]
            before = copy.deepcopy((stock, orders))
            with self.assertRaises(ValueError):
                allocate(stock, orders)
            self.assertEqual((stock, orders), before)

    def test_empty_order_identity(self):
        stock = {'A': 1}
        orders = [{'id': 'e', 'items': []}, {'id': 'e', 'items': []}]
        self.assertEqual(
            allocate(stock, orders),
            {'remaining': {'A': 1},
             'results': [{'id': 'e', 'accepted': True},
                         {'id': 'e', 'accepted': True}]},
        )
        with self.assertRaises(ValueError):
            allocate(stock, [{'id': 'e', 'items': []},
                             {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])

    def test_distinct_ids_still_consume_stock_separately(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 3}]}]
        self.assertEqual(
            allocate(stock, orders),
            {'remaining': {'A': 2},
             'results': [{'id': 'x', 'accepted': True},
                         {'id': 'y', 'accepted': False}]},
        )


if __name__ == '__main__':
    unittest.main()
