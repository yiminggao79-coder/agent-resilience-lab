import copy
import unittest

from allocation import Inventory, allocate


class InventoryTests(unittest.TestCase):
    def test_state_persists_across_calls(self):
        inventory = Inventory({'A': 5})
        first = inventory.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(first['remaining'], {'A': 3})
        second = inventory.allocate([{'id': 'y', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(second['remaining'], {'A': 1})
        self.assertEqual(inventory.snapshot(), {'A': 1})

    def test_replay_across_calls_does_not_consume_stock(self):
        inventory = Inventory({'A': 4})
        order = {'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]}
        inventory.allocate([order])
        replay = inventory.allocate([order])
        self.assertEqual(replay['remaining'], {'A': 2})
        self.assertEqual(replay['results'], [{'id': 'o', 'accepted': True}])

    def test_split_replay_is_same_identity(self):
        inventory = Inventory({'A': 2, 'B': 2})
        inventory.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                                 {'sku': 'B', 'qty': 1}]}])
        replay = inventory.allocate([{'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                                         {'sku': 'A', 'qty': 1}]}])
        self.assertEqual(replay['remaining'], {'A': 1, 'B': 1})
        self.assertEqual(replay['results'], [{'id': 'x', 'accepted': True}])

    def test_rejected_id_stays_rejected_across_calls(self):
        inventory = Inventory({'A': 2})
        inventory.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 9}]}])
        # Stock is replenished through a different id, but x stays rejected.
        inventory.allocate([{'id': 'y', 'items': [{'sku': 'A', 'qty': 0}]}])
        replay = inventory.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 9}]}])
        self.assertEqual(replay['results'], [{'id': 'x', 'accepted': False}])
        self.assertEqual(replay['remaining'], {'A': 2})

    def test_conflict_raises_and_inputs_unchanged(self):
        stock = {'A': 3}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            Inventory(stock).allocate(orders)
        self.assertEqual((stock, orders), before)
        self.assertEqual(stock, before[0])

    def test_failed_batch_is_atomic(self):
        inventory = Inventory({'A': 5})
        committed = inventory.allocate([{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(committed['remaining'], {'A': 4})
        with self.assertRaises(ValueError):
            inventory.allocate([{'id': 'b', 'items': [{'sku': 'A', 'qty': 1}]},
                                {'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]}])
        # Earlier committed batch persists; failed batch left no trace.
        self.assertEqual(inventory.snapshot(), {'A': 4})
        # 'b' was never remembered, so it can be used with a new payload.
        result = inventory.allocate([{'id': 'b', 'items': [{'sku': 'A', 'qty': 3}]}])
        self.assertEqual(result['results'], [{'id': 'b', 'accepted': True}])
        self.assertEqual(result['remaining'], {'A': 1})

    def test_failed_batch_keeps_replay_memory(self):
        inventory = Inventory({'A': 5})
        inventory.allocate([{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]}])
        with self.assertRaises(ValueError):
            inventory.allocate([{'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]},
                                {'id': 'a', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(inventory.snapshot(), {'A': 4})

    def test_snapshot_is_detached(self):
        inventory = Inventory({'A': 2})
        snap = inventory.snapshot()
        snap['A'] = 99
        snap['B'] = 5
        self.assertEqual(inventory.snapshot(), {'A': 2})
        inventory.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(snap, {'A': 99, 'B': 5})

    def test_returned_remaining_is_detached(self):
        inventory = Inventory({'A': 2})
        result = inventory.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        result['remaining']['A'] = 1000
        result['remaining']['Z'] = 1
        self.assertEqual(inventory.snapshot(), {'A': 1})
        result2 = inventory.allocate([{'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(result2['remaining'], {'A': 0})
        self.assertEqual(result2['results'], [{'id': 'y', 'accepted': True}])

    def test_constructor_stock_not_mutated(self):
        stock = {'A': 3, 'B': 0}
        before = copy.deepcopy(stock)
        inventory = Inventory(stock)
        inventory.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(stock, before)

    def test_order_inputs_not_mutated(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                       {'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy(orders)
        Inventory({'A': 3}).allocate(orders)
        self.assertEqual(orders, before)

    def test_module_allocate_still_fresh_per_call(self):
        stock = {'A': 4}
        order = {'id': 'o', 'items': [{'sku': 'A', 'qty': 2}]}
        self.assertEqual(allocate(stock, [order])['remaining'], {'A': 2})
        # A second call must not remember the id from the first.
        second = allocate(stock, [order])
        self.assertEqual(second['remaining'], {'A': 2})
        self.assertEqual(second['results'], [{'id': 'o', 'accepted': True}])
        self.assertEqual(stock, {'A': 4})

    def test_empty_order_persists_identity(self):
        inventory = Inventory({})
        self.assertEqual(inventory.allocate([{'id': 'e', 'items': []}]),
                         {'remaining': {}, 'results': [{'id': 'e', 'accepted': True}]})
        self.assertEqual(inventory.allocate([{'id': 'e', 'items': []}]),
                         {'remaining': {}, 'results': [{'id': 'e', 'accepted': True}]})
        with self.assertRaises(ValueError):
            inventory.allocate([{'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])

    def test_zero_stock_keys_preserved(self):
        inventory = Inventory({'A': 0, 'B': 2})
        result = inventory.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 0},
                                                          {'sku': 'B', 'qty': 1}]}])
        self.assertEqual(result['remaining'], {'A': 0, 'B': 1})


if __name__ == '__main__':
    unittest.main()
