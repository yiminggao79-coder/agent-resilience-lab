import copy
import unittest

from allocation import Inventory, allocate


class InventoryTests(unittest.TestCase):
    def test_stock_and_id_memory_persist_across_calls(self):
        inventory = Inventory({'A': 2, 'zero': 0})
        first = inventory.allocate([
            {'id': 'used', 'items': [{'sku': 'A', 'qty': 2}]},
            {'id': 'empty', 'items': []},
        ])
        self.assertEqual(first, {
            'remaining': {'A': 0, 'zero': 0},
            'results': [
                {'id': 'used', 'accepted': True},
                {'id': 'empty', 'accepted': True},
            ],
        })

        second = inventory.allocate([
            {'id': 'used', 'items': [{'sku': 'A', 'qty': 1},
                                     {'sku': 'A', 'qty': 1}]},
            {'id': 'later', 'items': [{'sku': 'A', 'qty': 1}]},
        ])
        self.assertEqual(second, {
            'remaining': {'A': 0, 'zero': 0},
            'results': [
                {'id': 'used', 'accepted': True},
                {'id': 'later', 'accepted': False},
            ],
        })

    def test_rejected_ids_stay_rejected_across_calls(self):
        inventory = Inventory({'A': 1})
        self.assertFalse(inventory.allocate([
            {'id': 'nope', 'items': [{'sku': 'A', 'qty': 2}]},
        ])['results'][0]['accepted'])
        # A later call has enough stock, but the earlier decision is remembered.
        inventory.allocate([])
        result = inventory.allocate([
            {'id': 'nope', 'items': [{'sku': 'A', 'qty': 2}]},
        ])
        self.assertEqual(result, {
            'remaining': {'A': 1},
            'results': [{'id': 'nope', 'accepted': False}],
        })

    def test_conflict_rolls_back_current_batch_only(self):
        stock = {'A': 3, 'B': 2}
        inventory = Inventory(stock)
        inventory.allocate([
            {'id': 'kept', 'items': [{'sku': 'A', 'qty': 1}]},
        ])
        batches = [
            {'id': 'new1', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'new2', 'items': [{'sku': 'B', 'qty': 1}]},
            {'id': 'kept', 'items': [{'sku': 'A', 'qty': 2}]},
        ]
        before = copy.deepcopy(batches)
        with self.assertRaises(ValueError):
            inventory.allocate(batches)
        self.assertEqual(batches, before)
        self.assertEqual(inventory.snapshot(), {'A': 2, 'B': 2})

        # The newly introduced IDs were rolled back and can be decided anew.
        result = inventory.allocate([
            {'id': 'new1', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'new2', 'items': [{'sku': 'B', 'qty': 1}]},
        ])
        self.assertEqual(result, {
            'remaining': {'A': 1, 'B': 1},
            'results': [
                {'id': 'new1', 'accepted': True},
                {'id': 'new2', 'accepted': True},
            ],
        })

    def test_conflict_on_replay_preserves_stock(self):
        inventory = Inventory({'A': 2})
        inventory.allocate([
            {'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
        ])
        with self.assertRaises(ValueError):
            inventory.allocate([
                {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
            ])
        self.assertEqual(inventory.snapshot(), {'A': 1})

    def test_snapshot_and_returned_remaining_are_detached(self):
        inventory = Inventory({'A': 2})
        snapshot = inventory.snapshot()
        snapshot['A'] = 99
        snapshot['extra'] = 1
        self.assertEqual(inventory.snapshot(), {'A': 2})

        result = inventory.allocate([
            {'id': 'one', 'items': [{'sku': 'A', 'qty': 1}]},
        ])
        result['remaining']['A'] = 99
        self.assertEqual(inventory.snapshot(), {'A': 1})

    def test_constructor_stock_and_caller_orders_are_not_mutated(self):
        stock = {'A': 2, 'zero': 0}
        batches = [
            [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}],
            [{'id': 'y', 'items': [{'sku': 'A', 'qty': 2}]}],
        ]
        stock_before = copy.deepcopy(stock)
        batches_before = copy.deepcopy(batches)
        inventory = Inventory(stock)
        for batch in batches:
            inventory.allocate(batch)
        self.assertEqual(stock, stock_before)
        self.assertEqual(batches, batches_before)

    def test_stateless_function_starts_fresh_each_call(self):
        stock = {'A': 1}
        orders = [{'id': 'same', 'items': [{'sku': 'A', 'qty': 1}]}]
        first = allocate(stock, orders)
        second = allocate(stock, orders)
        self.assertEqual(first, second)
        self.assertEqual(first, {
            'remaining': {'A': 0},
            'results': [{'id': 'same', 'accepted': True}],
        })

    def test_empty_batch_and_empty_order_identity(self):
        inventory = Inventory({'A': 1})
        self.assertEqual(inventory.allocate([]), {
            'remaining': {'A': 1}, 'results': [],
        })
        self.assertTrue(inventory.allocate([
            {'id': 'empty', 'items': []},
        ])['results'][0]['accepted'])
        # Replaying an empty order with items is a conflict within this call.
        with self.assertRaises(ValueError):
            inventory.allocate([
                {'id': 'empty', 'items': []},
                {'id': 'empty', 'items': [{'sku': 'A', 'qty': 1}]},
            ])
        self.assertEqual(inventory.snapshot(), {'A': 1})


if __name__ == '__main__':
    unittest.main()
