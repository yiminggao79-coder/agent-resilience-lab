import copy
import unittest

from manifest import constrained, packed

CATALOG = [
    {'sku': 'a', 'weight': 1, 'volume': 1},
    {'sku': 'b', 'weight': 1, 'volume': 1},
    {'sku': 'c', 'weight': 1, 'volume': 1},
]
BIG = [{'code': 'big', 'max_weight': 10, 'max_volume': 10}]


class IncompatibleTests(unittest.TestCase):
    def test_incompatible_pair_stays_in_separate_cartons(self):
        result = constrained(
            CATALOG,
            [{'sku': 'a', 'quantity': 1},
             {'sku': 'b', 'quantity': 1},
             {'sku': 'c', 'quantity': 1}],
            BIG,
            {'incompatible': [['a', 'b']]},
        )
        self.assertEqual(result['cartons'], [
            {'box': 'big', 'items': [
                {'sku': 'a', 'quantity': 1},
                {'sku': 'c', 'quantity': 1},
            ], 'weight': 2, 'volume': 2},
            {'box': 'big', 'items': [{'sku': 'b', 'quantity': 1}],
             'weight': 1, 'volume': 1},
        ])

    def test_repeated_and_reversed_pairs_are_harmless(self):
        result = constrained(
            CATALOG,
            [{'sku': 'a', 'quantity': 1}, {'sku': 'b', 'quantity': 1}],
            BIG,
            {'incompatible': [['a', 'b'], ['b', 'a'], ['a', 'b']]},
        )
        self.assertEqual(len(result['cartons']), 2)


class UnitCapTests(unittest.TestCase):
    def test_max_units_caps_the_total_units_in_a_carton(self):
        result = constrained(
            CATALOG,
            [{'sku': 'a', 'quantity': 3}, {'sku': 'b', 'quantity': 1}],
            BIG,
            {'max_units': 2},
        )
        self.assertEqual(result['cartons'], [
            {'box': 'big', 'items': [{'sku': 'a', 'quantity': 2}],
             'weight': 2, 'volume': 2},
            {'box': 'big', 'items': [
                {'sku': 'a', 'quantity': 1},
                {'sku': 'b', 'quantity': 1},
            ], 'weight': 2, 'volume': 2},
        ])


class FixedBoxTests(unittest.TestCase):
    def test_fixed_box_is_filtered_and_can_be_shared(self):
        boxes = [
            {'code': 'aaa', 'max_weight': 5, 'max_volume': 5},
            {'code': 'z', 'max_weight': 5, 'max_volume': 5},
        ]
        result = constrained(
            CATALOG,
            [{'sku': 'a', 'quantity': 1}, {'sku': 'b', 'quantity': 1}],
            boxes,
            {'fixed_box': {'a': 'z'}},
        )
        self.assertEqual(result['cartons'], [
            {'box': 'z', 'items': [
                {'sku': 'a', 'quantity': 1},
                {'sku': 'b', 'quantity': 1},
            ], 'weight': 2, 'volume': 2},
        ])

    def test_incompatible_skus_fixed_to_same_box_use_two_cartons(self):
        boxes = [{'code': 'shared', 'max_weight': 5, 'max_volume': 5}]
        result = constrained(
            CATALOG,
            [{'sku': 'a', 'quantity': 1}, {'sku': 'b', 'quantity': 1}],
            boxes,
            {
                'incompatible': [['a', 'b']],
                'fixed_box': {'a': 'shared', 'b': 'shared'},
            },
        )
        self.assertEqual(result['cartons'], [
            {'box': 'shared', 'items': [{'sku': 'a', 'quantity': 1}],
             'weight': 1, 'volume': 1},
            {'box': 'shared', 'items': [{'sku': 'b', 'quantity': 1}],
             'weight': 1, 'volume': 1},
        ])


class PolicyValidationTests(unittest.TestCase):
    def test_invalid_policies_are_rejected_even_for_empty_orders(self):
        cases = [
            [],
            42,
            {'unknown': 1},
            {'incompatible': 'a,b'},
            {'incompatible': [['a']]},
            {'incompatible': [['a', 'b', 'c']]},
            {'incompatible': [['a', 'a']]},
            {'incompatible': [[1, 'a']]},
            {'incompatible': [['a', 'missing']]},
            {'incompatible': [('a', 'b')]},
            {'max_units': 0},
            {'max_units': -1},
            {'max_units': True},
            {'max_units': 1.5},
            {'max_units': '1'},
            {'fixed_box': []},
            {'fixed_box': {'missing': 'big'}},
            {'fixed_box': {'a': 'missing'}},
            {'fixed_box': {1: 'big'}},
            {'fixed_box': {'a': 1}},
        ]
        for policy in cases:
            with self.subTest(policy=policy):
                with self.assertRaises(ValueError):
                    constrained(CATALOG, [], BIG, policy)

    def test_none_and_empty_policy_are_valid_for_empty_orders(self):
        expected = {
            'items': [],
            'totals': {'units': 0, 'weight': 0, 'volume': 0},
            'cartons': [],
        }
        self.assertEqual(constrained(CATALOG, [], BIG, None), expected)
        self.assertEqual(constrained(CATALOG, [], BIG, {}), expected)

    def test_packed_remains_unrestricted(self):
        lines = [{'sku': 'a', 'quantity': 1}, {'sku': 'b', 'quantity': 1}]
        self.assertEqual(packed(CATALOG, lines, BIG),
                         constrained(CATALOG, lines, BIG, None))


class PolicyOwnershipTests(unittest.TestCase):
    def test_pack_does_not_mutate_policy_or_share_records(self):
        lines = [{'sku': 'a', 'quantity': 2}, {'sku': 'b', 'quantity': 1}]
        boxes = [{'code': 'big', 'max_weight': 10, 'max_volume': 10}]
        policy = {
            'incompatible': [['a', 'b']],
            'max_units': 2,
            'fixed_box': {'a': 'big'},
        }
        catalog_before = copy.deepcopy(CATALOG)
        lines_before = copy.deepcopy(lines)
        boxes_before = copy.deepcopy(boxes)
        policy_before = copy.deepcopy(policy)

        result = constrained(CATALOG, lines, boxes, policy)
        self.assertEqual(CATALOG, catalog_before)
        self.assertEqual(lines, lines_before)
        self.assertEqual(boxes, boxes_before)
        self.assertEqual(policy, policy_before)

        result['cartons'][0]['items'][0]['quantity'] = 999
        self.assertEqual(policy, policy_before)
        self.assertEqual(lines, lines_before)


if __name__ == '__main__':
    unittest.main()
