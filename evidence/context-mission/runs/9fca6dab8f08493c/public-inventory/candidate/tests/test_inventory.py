import copy
import unittest

import allocation
from allocation import Inventory, allocate


class InventoryTests(unittest.TestCase):
    def test_exported_and_previous_function_unchanged(self):
        self.assertTrue(hasattr(allocation, 'Inventory'))
        # The function is per-call only: a second call starts from fresh stock
        # and forgets prior ID decisions.
        stock = {'A': 5}
        first = allocate(stock, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}])
        second = allocate(stock, [{'id': 'x', 'items': [{'sku': 'A', 'qty': 3}]}])
        self.assertEqual(first, second)
        self.assertEqual(first['remaining'], {'A': 2})
        self.assertEqual(stock, {'A': 5})

    def test_stock_persists_across_batches(self):
        inv = Inventory({'A': 5, 'zero': 0})
        r1 = inv.allocate([{'id': 'o1', 'items': [{'sku': 'A', 'qty': 3}]}])
        r2 = inv.allocate([{'id': 'o2', 'items': [{'sku': 'A', 'qty': 3}]}])
        self.assertEqual(r1['remaining'], {'A': 2, 'zero': 0})
        self.assertEqual(r2['remaining'], {'A': 2, 'zero': 0})
        self.assertEqual([row['accepted'] for row in r2['results']], [False])
        self.assertEqual(inv.snapshot(), {'A': 2, 'zero': 0})

    def test_replay_across_batches_consumes_nothing(self):
        inv = Inventory({'A': 5})
        inv.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}])
        replay = inv.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                                     {'sku': 'A', 'qty': 1}]}])
        self.assertEqual(replay['results'], [{'id': 'x', 'accepted': True}])
        self.assertEqual(replay['remaining'], {'A': 3})
        self.assertEqual(inv.snapshot(), {'A': 3})

    def test_rejected_id_stays_rejected_in_later_batch(self):
        inv = Inventory({'A': 1})
        inv.allocate([{'id': 'r', 'items': [{'sku': 'A', 'qty': 9}]}])
        later = inv.allocate([{'id': 'r', 'items': [{'sku': 'A', 'qty': 9}]},
                              {'id': 'ok', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(later['results'], [{'id': 'r', 'accepted': False},
                                            {'id': 'ok', 'accepted': True}])
        self.assertEqual(inv.snapshot(), {'A': 0})

    def test_conflict_across_batches_raises(self):
        inv = Inventory({'A': 5})
        inv.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        before = inv.snapshot()
        with self.assertRaises(ValueError):
            inv.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(inv.snapshot(), before)

    def test_failed_batch_is_atomic_no_stock_or_memory_leak(self):
        inv = Inventory({'A': 5, 'B': 5})
        inv.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        before = inv.snapshot()
        with self.assertRaises(ValueError):
            inv.allocate([
                {'id': 'new', 'items': [{'sku': 'A', 'qty': 2}]},
                {'id': 'y', 'items': [{'sku': 'B', 'qty': 1}]},
                {'id': 'x', 'items': [{'sku': 'A', 'qty': 9}]},
            ])
        # No stock change from the earlier orders of the failed batch.
        self.assertEqual(inv.snapshot(), before)
        # 'new' and 'y' were not remembered, so they may be used fresh later.
        after = inv.allocate([{'id': 'new', 'items': [{'sku': 'A', 'qty': 2}]},
                              {'id': 'y', 'items': [{'sku': 'B', 'qty': 1}]}])
        self.assertEqual([row['accepted'] for row in after['results']],
                         [True, True])
        self.assertEqual(inv.snapshot(), {'A': 2, 'B': 4})

    def test_earlier_successful_batches_stay_committed_after_failure(self):
        inv = Inventory({'A': 5})
        inv.allocate([{'id': 'k', 'items': [{'sku': 'A', 'qty': 2}]}])
        with self.assertRaises(ValueError):
            inv.allocate([{'id': 'k', 'items': [{'sku': 'A', 'qty': 3}]}])
        self.assertEqual(inv.snapshot(), {'A': 3})
        self.assertEqual(inv.allocate([{'id': 'm', 'items': [{'sku': 'A', 'qty': 3}]}])
                         ['results'], [{'id': 'm', 'accepted': True}])

    def test_constructor_stock_not_mutated_and_detached(self):
        stock = {'A': 3, 'zero': 0}
        before = copy.deepcopy(stock)
        inv = Inventory(stock)
        inv.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(stock, before)
        stock['A'] = 99
        self.assertEqual(inv.snapshot(), {'A': 2, 'zero': 0})

    def test_order_inputs_not_mutated(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'A', 'qty': 1}]}]
        before = copy.deepcopy(orders)
        Inventory({'A': 5}).allocate(orders)
        self.assertEqual(orders, before)

    def test_snapshot_is_detached(self):
        inv = Inventory({'A': 5})
        snap = inv.snapshot()
        snap['A'] = 0
        snap['new'] = 1
        self.assertEqual(inv.snapshot(), {'A': 5})
        inv.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(snap['A'], 0)

    def test_returned_remaining_is_detached(self):
        inv = Inventory({'A': 5, 'zero': 0})
        result = inv.allocate([{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}])
        result['remaining']['A'] = 0
        result['remaining']['injected'] = 7
        self.assertEqual(inv.snapshot(), {'A': 3, 'zero': 0})
        later = inv.allocate([{'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(later['remaining'], {'A': 2, 'zero': 0})

    def test_row_schema_and_preserved_zero_keys(self):
        inv = Inventory({'B': 0, 'A': 1})
        result = inv.allocate([{'id': 'a', 'items': []},
                               {'id': 'b', 'items': [{'sku': 'B', 'qty': 1}]}])
        self.assertEqual(result['results'],
                         [{'id': 'a', 'accepted': True},
                          {'id': 'b', 'accepted': False}])
        self.assertEqual(set(result['remaining']), {'A', 'B'})
        for row in result['results']:
            self.assertEqual(set(row), {'id', 'accepted'})

    def test_empty_batch_is_noop(self):
        inv = Inventory({'A': 2})
        result = inv.allocate([])
        self.assertEqual(result, {'remaining': {'A': 2}, 'results': []})
        self.assertEqual(inv.snapshot(), {'A': 2})

    def test_empty_order_identity_across_batches(self):
        inv = Inventory({'A': 0})
        inv.allocate([{'id': 'e', 'items': []}])
        replay = inv.allocate([{'id': 'e', 'items': []}])
        self.assertEqual(replay['results'], [{'id': 'e', 'accepted': True}])
        with self.assertRaises(ValueError):
            inv.allocate([{'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(inv.snapshot(), {'A': 0})


if __name__ == '__main__':
    unittest.main()
