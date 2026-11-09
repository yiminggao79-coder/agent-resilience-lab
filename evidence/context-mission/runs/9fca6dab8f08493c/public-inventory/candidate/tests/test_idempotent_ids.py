import copy
import unittest

from allocation import allocate


class IdempotentIdTests(unittest.TestCase):
    def test_same_id_same_quantities_replays_without_consuming(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 5}, orders)
        self.assertEqual(result['remaining'], {'A': 3})
        self.assertEqual(result['results'], [{'id': 'x', 'accepted': True},
                                             {'id': 'x', 'accepted': True}])

    def test_line_order_and_splitting_do_not_change_identity(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 2}]},
                  {'id': 'x', 'items': [{'sku': 'B', 'qty': 1},
                                        {'sku': 'A', 'qty': 1},
                                        {'sku': 'B', 'qty': 1}]}]
        result = allocate({'A': 1, 'B': 2}, orders)
        self.assertEqual(result['remaining'], {'A': 0, 'B': 0})
        self.assertEqual([row['accepted'] for row in result['results']],
                         [True, True])

    def test_rejected_id_stays_rejected_on_replay(self):
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'y', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 1}, orders)
        self.assertEqual([row['accepted'] for row in result['results']],
                         [False, True, False])
        self.assertEqual(result['remaining'], {'A': 0})

    def test_conflicting_reuse_raises_and_does_not_mutate(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 2}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_conflict_detected_before_earlier_mutation(self):
        stock = {'A': 5}
        orders = [{'id': 'x', 'items': [{'sku': 'A', 'qty': 1}]},
                  {'id': 'x', 'items': [{'sku': 'A', 'qty': 9}]}]
        before = copy.deepcopy((stock, orders))
        with self.assertRaises(ValueError):
            allocate(stock, orders)
        self.assertEqual((stock, orders), before)

    def test_empty_order_identity(self):
        orders = [{'id': 'e', 'items': []}, {'id': 'e', 'items': []}]
        result = allocate({'A': 0}, orders)
        self.assertEqual(result, {'remaining': {'A': 0},
                                  'results': [{'id': 'e', 'accepted': True},
                                              {'id': 'e', 'accepted': True}]})

    def test_empty_order_conflicts_with_nonempty_id(self):
        with self.assertRaises(ValueError):
            allocate({'A': 1}, [{'id': 'e', 'items': []},
                                {'id': 'e', 'items': [{'sku': 'A', 'qty': 1}]}])

    def test_distinct_ids_consume_independently(self):
        orders = [{'id': 'p', 'items': [{'sku': 'A', 'qty': 2}]},
                  {'id': 'q', 'items': [{'sku': 'A', 'qty': 2}]}]
        result = allocate({'A': 3}, orders)
        self.assertEqual([row['accepted'] for row in result['results']],
                         [True, False])
        self.assertEqual(result['remaining'], {'A': 1})


if __name__ == '__main__':
    unittest.main()
