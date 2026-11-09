import unittest

from allocation import Inventory, allocate


class InventoryTests(unittest.TestCase):
    def test_existing_function_uses_fresh_inventory_each_call(self):
        stock = {'A': 2}
        first = allocate(stock, [{'id': 'order', 'items': [{'sku': 'A', 'qty': 2}]}])
        second = allocate(stock, [{'id': 'order', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertTrue(first['results'][0]['accepted'])
        self.assertTrue(second['results'][0]['accepted'])
        self.assertEqual(second['remaining'], {'A': 0})
        self.assertEqual(stock, {'A': 2})

    def test_stock_and_replay_memory_persist_across_calls(self):
        inventory = Inventory({'A': 5})
        first = inventory.allocate(
            [{'id': 'one', 'items': [{'sku': 'A', 'qty': 3}]}]
        )
        replay = inventory.allocate(
            [{'id': 'one', 'items': [{'sku': 'A', 'qty': 3}]}]
        )
        later = inventory.allocate(
            [{'id': 'two', 'items': [{'sku': 'A', 'qty': 1}]}]
        )
        self.assertEqual(first['remaining'], {'A': 2})
        self.assertEqual(replay['remaining'], {'A': 2})
        self.assertEqual(later['remaining'], {'A': 1})
        self.assertEqual(inventory.snapshot(), {'A': 1})

    def test_rejected_id_stays_rejected_on_replay(self):
        inventory = Inventory({'A': 1})
        rejected = inventory.allocate(
            [{'id': 'too-big', 'items': [{'sku': 'A', 'qty': 2}]}]
        )
        replay = inventory.allocate(
            [{'id': 'too-big', 'items': [{'sku': 'A', 'qty': 2}]}]
        )
        self.assertFalse(rejected['results'][0]['accepted'])
        self.assertFalse(replay['results'][0]['accepted'])
        self.assertEqual(inventory.snapshot(), {'A': 1})

    def test_split_lines_have_same_identity_across_calls(self):
        inventory = Inventory({'A': 5})
        inventory.allocate(
            [
                {
                    'id': 'split',
                    'items': [
                        {'sku': 'A', 'qty': 2},
                        {'sku': 'A', 'qty': 1},
                    ],
                }
            ]
        )
        replay = inventory.allocate(
            [{'id': 'split', 'items': [{'sku': 'A', 'qty': 3}]}]
        )
        self.assertTrue(replay['results'][0]['accepted'])
        self.assertEqual(replay['remaining'], {'A': 2})
        self.assertEqual(inventory.snapshot(), {'A': 2})

    def test_conflict_rolls_back_only_the_failed_batch(self):
        inventory = Inventory({'A': 4})
        inventory.allocate(
            [{'id': 'committed', 'items': [{'sku': 'A', 'qty': 1}]}]
        )

        with self.assertRaises(ValueError):
            inventory.allocate(
                [
                    {'id': 'new', 'items': [{'sku': 'A', 'qty': 1}]},
                    {'id': 'committed', 'items': [{'sku': 'A', 'qty': 2}]},
                ]
            )

        # The failed batch's stock deduction and new ID were not committed.
        self.assertEqual(inventory.snapshot(), {'A': 3})
        replay = inventory.allocate(
            [{'id': 'new', 'items': [{'sku': 'A', 'qty': 2}]}]
        )
        self.assertTrue(replay['results'][0]['accepted'])
        self.assertEqual(inventory.snapshot(), {'A': 1})

    def test_returned_remaining_and_snapshot_are_detached(self):
        inventory = Inventory({'A': 3, 'B': 0})
        result = inventory.allocate(
            [{'id': 'one', 'items': [{'sku': 'A', 'qty': 1}]}]
        )
        result['remaining']['A'] = 99
        snapshot = inventory.snapshot()
        snapshot['A'] = 77
        self.assertEqual(inventory.snapshot(), {'A': 2, 'B': 0})

    def test_empty_order_identity_and_conflict(self):
        inventory = Inventory({'A': 1})
        empty = inventory.allocate([{'id': 'empty', 'items': []}])
        replay = inventory.allocate([{'id': 'empty', 'items': []}])
        self.assertTrue(empty['results'][0]['accepted'])
        self.assertTrue(replay['results'][0]['accepted'])
        with self.assertRaises(ValueError):
            inventory.allocate(
                [{'id': 'empty', 'items': [{'sku': 'A', 'qty': 1}]}]
            )
        self.assertEqual(inventory.snapshot(), {'A': 1})

    def test_inputs_are_not_mutated(self):
        stock = {'A': 2, 'B': 0}
        orders = [
            {'id': 'one', 'items': [{'sku': 'A', 'qty': 1}]},
            {'id': 'two', 'items': [{'sku': 'B', 'qty': 1}]},
        ]
        inventory = Inventory(stock)
        inventory.allocate(orders)
        self.assertEqual(stock, {'A': 2, 'B': 0})
        self.assertEqual(
            orders,
            [
                {'id': 'one', 'items': [{'sku': 'A', 'qty': 1}]},
                {'id': 'two', 'items': [{'sku': 'B', 'qty': 1}]},
            ],
        )


if __name__ == '__main__':
    unittest.main()
