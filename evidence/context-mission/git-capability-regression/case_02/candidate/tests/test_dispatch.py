import copy
import unittest

from dispatch import allocate
from manifest import constrained, dispatch_report

CATALOG = [
    {'sku': 'a', 'weight': 1, 'volume': 1},
    {'sku': 'b', 'weight': 1, 'volume': 1},
]
BOXES = [{'code': 'box', 'max_weight': 10, 'max_volume': 10}]


class DispatchOrderTests(unittest.TestCase):
    def test_priorities_are_stable_and_accepted_keep_processing_order(self):
        stock = {'a': 5, 'b': 5}
        requests = [
            {'id': 'low-first', 'destination': 'd1',
             'lines': [{'sku': 'a', 'quantity': 1}]},
            {'id': 'high-first', 'destination': 'd2', 'priority': 5,
             'lines': [{'sku': 'a', 'quantity': 1}]},
            {'id': 'low-second', 'destination': 'd3',
             'lines': [{'sku': 'b', 'quantity': 1}]},
            {'id': 'high-second', 'destination': 'd4', 'priority': 5,
             'lines': [{'sku': 'b', 'quantity': 1}]},
        ]

        result = allocate(CATALOG, stock, requests, BOXES)

        self.assertEqual(
            [item['id'] for item in result['accepted']],
            ['high-first', 'high-second', 'low-first', 'low-second'],
        )
        self.assertEqual(result['rejected'], [])
        self.assertEqual(result['remaining'], {'a': 3, 'b': 3})
        self.assertEqual(
            result['totals'],
            {'units': 4, 'cartons': 4, 'weight': 4, 'volume': 4},
        )

    def test_repeated_lines_reserve_the_whole_aggregated_quantity(self):
        requests = [
            {'id': 'too-many', 'destination': 'd', 'priority': 10,
             'lines': [{'sku': 'a', 'quantity': 1},
                       {'sku': 'a', 'quantity': 1}]},
            {'id': 'exact', 'destination': 'd',
             'lines': [{'sku': 'a', 'quantity': 1}]},
            {'id': 'none-left', 'destination': 'd', 'priority': -1,
             'lines': [{'sku': 'a', 'quantity': 1}]},
        ]

        result = allocate(CATALOG, {'a': 1}, requests, BOXES)

        self.assertEqual([item['id'] for item in result['accepted']], ['exact'])
        self.assertEqual(result['rejected'], [
            {'id': 'too-many', 'reason': 'stock'},
            {'id': 'none-left', 'reason': 'stock'},
        ])
        self.assertEqual(result['remaining'], {'a': 0, 'b': 0})
        self.assertEqual(result['totals']['units'], 1)

    def test_unpackable_request_consumes_no_stock(self):
        catalog = [
            {'sku': 'a', 'weight': 2, 'volume': 1},
            {'sku': 'b', 'weight': 1, 'volume': 1},
        ]
        boxes = [
            {'code': 'small', 'max_weight': 1, 'max_volume': 1},
            {'code': 'big', 'max_weight': 10, 'max_volume': 10},
        ]
        policy = {'fixed_box': {'a': 'small'}}
        requests = [
            {'id': 'bad', 'destination': 'd', 'priority': 5,
             'lines': [{'sku': 'a', 'quantity': 1}]},
            {'id': 'good', 'destination': 'd',
             'lines': [{'sku': 'b', 'quantity': 1}]},
        ]

        result = allocate(catalog, {'a': 1, 'b': 1}, requests, boxes, policy)

        self.assertEqual([item['id'] for item in result['accepted']], ['good'])
        self.assertEqual(result['rejected'], [{'id': 'bad', 'reason': 'unpackable'}])
        self.assertEqual(result['remaining'], {'a': 1, 'b': 0})
        self.assertEqual(result['accepted'][0]['manifest']['cartons'][0]['box'], 'small')
        self.assertEqual(result['totals'],
                         {'units': 1, 'cartons': 1, 'weight': 1, 'volume': 1})

    def test_totals_accumulate_across_accepted_manifests(self):
        catalog = [
            {'sku': 'a', 'weight': 2, 'volume': 1},
            {'sku': 'b', 'weight': 1, 'volume': 1},
        ]
        boxes = [{'code': 'one', 'max_weight': 2, 'max_volume': 1}]
        requests = [
            {'id': 'first', 'destination': 'd',
             'lines': [{'sku': 'a', 'quantity': 1},
                       {'sku': 'b', 'quantity': 2}]},
            {'id': 'second', 'destination': 'd',
             'lines': [{'sku': 'a', 'quantity': 1}]},
        ]

        result = allocate(catalog, {'a': 2, 'b': 2}, requests, boxes)

        self.assertEqual(len(result['accepted']), 2)
        self.assertEqual(result['totals'],
                         {'units': 4, 'cartons': 4, 'weight': 6, 'volume': 4})
        self.assertEqual(result['remaining'], {'a': 0, 'b': 0})


class DispatchPolicyTests(unittest.TestCase):
    def test_policy_restrictions_apply_to_each_accepted_manifest(self):
        catalog = [
            {'sku': 'a', 'weight': 1, 'volume': 1},
            {'sku': 'b', 'weight': 1, 'volume': 1},
            {'sku': 'c', 'weight': 1, 'volume': 1},
        ]
        boxes = [{'code': 'big', 'max_weight': 10, 'max_volume': 10}]
        policy = {
            'incompatible': [['a', 'b']],
            'max_units': 2,
        }
        requests = [
            {'id': 'mixed', 'destination': 'd',
             'lines': [{'sku': 'a', 'quantity': 2},
                       {'sku': 'b', 'quantity': 1},
                       {'sku': 'c', 'quantity': 1}]},
        ]

        result = allocate(
            catalog, {'a': 2, 'b': 1, 'c': 1}, requests, boxes, policy
        )

        self.assertEqual(result['rejected'], [])
        manifest = result['accepted'][0]['manifest']
        self.assertEqual(manifest['cartons'], [
            {'box': 'big', 'items': [{'sku': 'a', 'quantity': 2}],
             'weight': 2, 'volume': 2},
            {'box': 'big', 'items': [{'sku': 'b', 'quantity': 1},
                                      {'sku': 'c', 'quantity': 1}],
             'weight': 2, 'volume': 2},
        ])
        self.assertEqual(result['totals'],
                         {'units': 4, 'cartons': 2, 'weight': 4, 'volume': 4})

    def test_manifest_dispatch_report_matches_allocate(self):
        catalog = [
            {'sku': 'a', 'weight': 2, 'volume': 1},
            {'sku': 'b', 'weight': 1, 'volume': 1},
        ]
        boxes = [{'code': 'box', 'max_weight': 10, 'max_volume': 10}]
        stock = {'a': 1}
        requests = [
            {'id': 'one', 'destination': 'd',
             'lines': [{'sku': 'a', 'quantity': 1}]},
        ]

        result = dispatch_report(catalog, stock, requests, boxes)

        self.assertEqual(result, allocate(catalog, stock, requests, boxes))
        self.assertEqual(
            result['accepted'][0]['manifest'],
            constrained(catalog, requests[0]['lines'], boxes),
        )


class DispatchValidationTests(unittest.TestCase):
    def test_malformed_late_request_rejects_the_whole_call(self):
        valid = {'id': 'first', 'destination': 'd', 'lines': []}
        bad_records = [
            None,
            {'destination': 'd', 'lines': []},
            {'id': '  ', 'destination': 'd', 'lines': []},
            {'id': 'x', 'destination': '', 'lines': []},
            {'id': 'x', 'destination': ' d', 'lines': []},
            {'id': 'x', 'destination': 'd'},
            {'id': 'x', 'destination': 'd', 'lines': ['bad']},
            {'id': 'x', 'destination': 'd',
             'lines': [{'sku': 'z', 'quantity': 1}]},
            {'id': 'x', 'destination': 'd',
             'lines': [{'sku': 'a', 'quantity': 0}]},
            {'id': 'x', 'destination': 'd', 'priority': True, 'lines': []},
            {'id': 'first', 'destination': 'd', 'lines': []},
        ]
        stock = {'a': 1, 'b': 1}
        stock_before = copy.deepcopy(stock)

        for bad in bad_records:
            with self.subTest(bad=bad):
                requests = [valid, bad]
                requests_before = copy.deepcopy(requests)
                with self.assertRaises(ValueError):
                    allocate(CATALOG, stock, requests, BOXES)
                self.assertEqual(stock, stock_before)
                self.assertEqual(requests, requests_before)

    def test_all_inputs_are_validated_before_empty_processing(self):
        cases = [
            ([{'sku': 'a', 'weight': 1, 'volume': 1},
              {'sku': 'a', 'weight': 2, 'volume': 2}], {}, [], None),
            (CATALOG, {'unknown': 1}, [], None),
            (CATALOG, {'a': -1}, [], None),
            (CATALOG, {'a': True}, [], None),
            (CATALOG, {'a': 1}, [{'code': 'x', 'max_weight': 0,
                                  'max_volume': 1}], None),
            (CATALOG, {'a': 1}, BOXES, {'max_units': 0}),
        ]

        for catalog, stock, boxes, policy in cases:
            with self.subTest(stock=stock, boxes=boxes, policy=policy):
                with self.assertRaises(ValueError):
                    allocate(catalog, stock, [], boxes, policy)

    def test_inputs_are_not_mutated_on_success(self):
        stock = {'a': 2}
        requests = [
            {'id': 'one', 'destination': 'd', 'priority': 2,
             'lines': [{'sku': 'a', 'quantity': 1}]},
        ]
        policy = {'max_units': 2}
        stock_before = copy.deepcopy(stock)
        requests_before = copy.deepcopy(requests)
        policy_before = copy.deepcopy(policy)

        result = allocate(CATALOG, stock, requests, BOXES, policy)
        result['accepted'][0]['manifest']['items'][0]['quantity'] = 99
        result['remaining']['a'] = 99

        self.assertEqual(stock, stock_before)
        self.assertEqual(requests, requests_before)
        self.assertEqual(policy, policy_before)


if __name__ == '__main__':
    unittest.main()
