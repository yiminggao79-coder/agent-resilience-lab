import unittest

from allocation import Inventory, allocate


class InventoryStateTests(unittest.TestCase):
    def test_state_persists_across_batches(self):
        inv = Inventory({'A': 5})
        first = inv.allocate([{'id': 'o1', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(first['remaining'], {'A': 3})
        second = inv.allocate([{'id': 'o2', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(second['remaining'], {'A': 1})
        self.assertEqual(inv.snapshot(), {'A': 1})

    def test_replay_across_calls_consumes_nothing(self):
        inv = Inventory({'A': 5})
        payload = [{'sku': 'A', 'qty': 2}]
        inv.allocate([{'id': 'o1', 'items': payload}])
        replay = inv.allocate([{'id': 'o2', 'items': [{'sku': 'A', 'qty': 1}]},
                               {'id': 'o1', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual([row['accepted'] for row in replay['results']], [True, True])
        self.assertEqual(replay['remaining'], {'A': 2})
        self.assertEqual(inv.snapshot(), {'A': 2})

    def test_rejected_id_stays_rejected_across_calls(self):
        inv = Inventory({'A': 1})
        inv.allocate([{'id': 'big', 'items': [{'sku': 'A', 'qty': 5}]}])
        # Restock directly so it *would* succeed if re-evaluated.
        inv._stock['A'] = 10
        later = inv.allocate([{'id': 'big', 'items': [{'sku': 'A', 'qty': 5}]}])
        self.assertEqual(later['results'], [{'id': 'big', 'accepted': False}])
        self.assertEqual(inv.snapshot(), {'A': 10})

    def test_conflict_rolls_back_whole_batch(self):
        inv = Inventory({'A': 5})
        inv.allocate([{'id': 'o1', 'items': [{'sku': 'A', 'qty': 1}]}])
        with self.assertRaises(ValueError):
            inv.allocate([
                {'id': 'new', 'items': [{'sku': 'A', 'qty': 2}]},
                {'id': 'o1', 'items': [{'sku': 'A', 'qty': 9}]},
            ])
        # Neither the stock deduction nor the "new" id may persist.
        self.assertEqual(inv.snapshot(), {'A': 4})
        retry = inv.allocate([{'id': 'new', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(retry['results'], [{'id': 'new', 'accepted': True}])
        self.assertEqual(inv.snapshot(), {'A': 2})

    def test_earlier_successful_batches_stay_committed(self):
        inv = Inventory({'A': 5})
        inv.allocate([{'id': 'a', 'items': [{'sku': 'A', 'qty': 3}]}])
        with self.assertRaises(ValueError):
            inv.allocate([{'id': 'b', 'items': [{'sku': 'A', 'qty': 1}]},
                          {'id': 'a', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(inv.snapshot(), {'A': 2})
        # "a" keeps its original decision and quantities.
        ok = inv.allocate([{'id': 'a', 'items': [{'sku': 'A', 'qty': 3}]}])
        self.assertEqual(ok['results'], [{'id': 'a', 'accepted': True}])
        self.assertEqual(inv.snapshot(), {'A': 2})

    def test_constructor_stock_not_mutated(self):
        stock = {'A': 3, 'B': 0}
        inv = Inventory(stock)
        inv.allocate([{'id': 'o1', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(stock, {'A': 3, 'B': 0})

    def test_snapshot_is_detached(self):
        inv = Inventory({'A': 3})
        snap = inv.snapshot()
        snap['A'] = 99
        snap['B'] = 1
        self.assertEqual(inv.snapshot(), {'A': 3})
        self.assertEqual(inv.allocate([])['remaining'], {'A': 3})

    def test_returned_remaining_is_detached(self):
        inv = Inventory({'A': 3})
        out = inv.allocate([{'id': 'o1', 'items': [{'sku': 'A', 'qty': 1}]}])
        out['remaining']['A'] = 100
        out['remaining']['Z'] = 5
        self.assertEqual(inv.snapshot(), {'A': 2})
        nxt = inv.allocate([{'id': 'o2', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(nxt['results'], [{'id': 'o2', 'accepted': True}])
        self.assertEqual(inv.snapshot(), {'A': 0})

    def test_order_inputs_not_mutated(self):
        orders = [{'id': 'o1', 'items': [{'sku': 'A', 'qty': 1},
                                         {'sku': 'A', 'qty': 1}]}]
        inv = Inventory({'A': 5})
        inv.allocate(orders)
        self.assertEqual(orders, [{'id': 'o1',
                                   'items': [{'sku': 'A', 'qty': 1},
                                             {'sku': 'A', 'qty': 1}]}])

    def test_empty_orders_and_zero_keys(self):
        inv = Inventory({'A': 0, 'B': 1})
        out = inv.allocate([{'id': 'e', 'items': []},
                            {'id': 'z', 'items': [{'sku': 'A', 'qty': 1}]}])
        self.assertEqual(out['results'], [{'id': 'e', 'accepted': True},
                                          {'id': 'z', 'accepted': False}])
        self.assertEqual(out['remaining'], {'A': 0, 'B': 1})
        again = inv.allocate([{'id': 'e', 'items': []}])
        self.assertEqual(again['results'], [{'id': 'e', 'accepted': True}])

    def test_function_uses_fresh_inventory(self):
        stock = {'A': 5}
        first = allocate(stock, [{'id': 'o1', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(first['remaining'], {'A': 3})
        # No memory carried over: the same id is re-evaluated on fresh stock.
        second = allocate(stock, [{'id': 'o1', 'items': [{'sku': 'A', 'qty': 3}]}])
        self.assertEqual(second['results'], [{'id': 'o1', 'accepted': True}])
        self.assertEqual(second['remaining'], {'A': 2})
        self.assertEqual(stock, {'A': 5})

    def test_function_error_leaves_stock_untouched(self):
        stock = {'A': 5}
        with self.assertRaises(ValueError):
            allocate(stock, [{'id': 'o1', 'items': [{'sku': 'A', 'qty': 1}]},
                             {'id': 'o1', 'items': [{'sku': 'A', 'qty': 2}]}])
        self.assertEqual(stock, {'A': 5})

    def test_split_lines_identity_across_calls(self):
        inv = Inventory({'A': 4})
        inv.allocate([{'id': 'o1', 'items': [{'sku': 'A', 'qty': 1},
                                             {'sku': 'A', 'qty': 2}]}])
        replay = inv.allocate([{'id': 'o1', 'items': [{'sku': 'A', 'qty': 3}]}])
        self.assertEqual(replay['results'], [{'id': 'o1', 'accepted': True}])
        self.assertEqual(replay['remaining'], {'A': 1})


if __name__ == '__main__':
    unittest.main()
