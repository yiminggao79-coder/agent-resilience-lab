import copy
import unittest
from manifest import constrained, make, packed
from packing import pack
from policies import validate


C = [
    {'sku': 'a', 'weight': 1, 'volume': 1},
    {'sku': 'b', 'weight': 1, 'volume': 1},
    {'sku': 'c', 'weight': 1, 'volume': 1},
    {'sku': 'd', 'weight': 1, 'volume': 1},
]


def box(code='big', max_weight=100, max_volume=100):
    return {'code': code, 'max_weight': max_weight, 'max_volume': max_volume}


def lines(*specs):
    return [{'sku': sku, 'quantity': quantity} for sku, quantity in specs]


class PolicyRegressions(unittest.TestCase):
    def test_incompatible_pairs_are_symmetric_and_repeats_are_harmless(self):
        cartons = pack(C, lines(('a', 1), ('b', 1), ('c', 1)), [box()],
                       {'incompatible': [['a', 'b'], ['b', 'a'], ['a', 'b']]})
        self.assertEqual(cartons, [
            {'box': 'big',
             'items': [{'sku': 'a', 'quantity': 1}, {'sku': 'c', 'quantity': 1}],
             'weight': 2, 'volume': 2},
            {'box': 'big', 'items': [{'sku': 'b', 'quantity': 1}],
             'weight': 1, 'volume': 1},
        ])

    def test_combined_cap_conflict_and_fixed_boxes(self):
        policy = {
            'incompatible': [['a', 'b']],
            'max_units': 2,
            'fixed_box': {'a': 'x', 'b': 'y', 'c': 'x'},
        }
        cartons = pack(C, lines(('a', 1), ('b', 1), ('c', 1), ('d', 1)),
                       [box('y'), box('x')], policy)
        self.assertEqual(cartons, [
            {'box': 'x',
             'items': [{'sku': 'a', 'quantity': 1}, {'sku': 'c', 'quantity': 1}],
             'weight': 2, 'volume': 2},
            {'box': 'y',
             'items': [{'sku': 'b', 'quantity': 1}, {'sku': 'd', 'quantity': 1}],
             'weight': 2, 'volume': 2},
        ])

    def test_fixed_box_reuse_and_free_skus_can_share(self):
        cartons = pack(C, lines(('a', 2), ('b', 1)), [box('y'), box('x')],
                       {'fixed_box': {'a': 'x'}})
        self.assertEqual(cartons, [
            {'box': 'x',
             'items': [{'sku': 'a', 'quantity': 2}, {'sku': 'b', 'quantity': 1}],
             'weight': 3, 'volume': 3},
        ])

    def test_fixed_sku_does_not_join_another_box(self):
        cartons = pack(C, lines(('a', 1), ('b', 2)), [box('x'), box('y')],
                       {'fixed_box': {'b': 'y'}})
        self.assertEqual(cartons, [
            {'box': 'x', 'items': [{'sku': 'a', 'quantity': 1}],
             'weight': 1, 'volume': 1},
            {'box': 'y', 'items': [{'sku': 'b', 'quantity': 2}],
             'weight': 2, 'volume': 2},
        ])

    def test_incompatible_fixed_choices_must_use_separate_cartons(self):
        policy = {'fixed_box': {'a': 'x', 'b': 'x'},
                  'incompatible': [['a', 'b']]}
        cartons = pack(C, lines(('a', 1), ('b', 1)), [box('x')], policy)
        self.assertEqual(cartons, [
            {'box': 'x', 'items': [{'sku': 'a', 'quantity': 1}],
             'weight': 1, 'volume': 1},
            {'box': 'x', 'items': [{'sku': 'b', 'quantity': 1}],
             'weight': 1, 'volume': 1},
        ])

    def test_impossible_fixed_box_raises(self):
        with self.assertRaises(ValueError):
            pack(C, lines(('a', 1)), [box('small', max_weight=1, max_volume=1)],
                 {'fixed_box': {'a': 'large'}})

    def test_invalid_policies_are_rejected_for_empty_orders(self):
        invalid = [
            [],
            {'bogus': []},
            {'incompatible': 'a,b'},
            {'incompatible': [['a']]},
            {'incompatible': [['a', 'a']]},
            {'incompatible': [['a', 'z']]},
            {'incompatible': [[1, 'b']]},
            {'max_units': 0},
            {'max_units': -1},
            {'max_units': True},
            {'max_units': '2'},
            {'max_units': 2.0},
            {'fixed_box': []},
            {'fixed_box': {'z': 'x'}},
            {'fixed_box': {'a': 'z'}},
            {'fixed_box': {1: 'x'}},
            {'fixed_box': {'a': 1}},
        ]
        for policy in invalid:
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                pack(C, [], [box('x')], policy)

    def test_empty_valid_policy_and_none_match_packed(self):
        self.assertEqual(pack(C, [], [box()], {}), [])
        self.assertEqual(constrained(C, [], [box()]), packed(C, [], [box()]))
        self.assertEqual(constrained(C, [], [box()], None),
                         packed(C, [], [box()]))

    def test_constrained_report_matches_pack_and_inputs_are_preserved(self):
        catalog = copy.deepcopy(C)
        order = lines(('a', 2), ('b', 1), ('c', 1))
        boxes = [box('x'), box('y')]
        policy = {'incompatible': [['a', 'b']], 'max_units': 2,
                  'fixed_box': {'b': 'y'}}
        before = (copy.deepcopy(catalog), copy.deepcopy(order),
                  copy.deepcopy(boxes), copy.deepcopy(policy))
        first = constrained(catalog, order, boxes, policy)
        self.assertEqual((catalog, order, boxes, policy), before)
        self.assertEqual(first['items'], make(C, order)['items'])
        self.assertEqual(first['totals'], make(C, order)['totals'])
        self.assertEqual(first['cartons'], pack(C, order, boxes, policy))
        first['items'][0]['quantity'] = 999
        first['cartons'][0]['items'][0]['quantity'] = 999
        second = constrained(catalog, order, boxes, policy)
        self.assertEqual(second['items'], make(C, order)['items'])
        self.assertEqual(second['cartons'], pack(C, order, boxes, policy))

    def test_compiled_policy_is_normalized(self):
        compiled = validate({'incompatible': [['a', 'b'], ['b', 'a']]},
                            C, [box()])
        self.assertEqual(compiled['incompatible']['a'], frozenset({'b'}))
        self.assertEqual(compiled['incompatible']['b'], frozenset({'a'}))
        self.assertIsNone(compiled['max_units'])
        self.assertEqual(compiled['fixed_box'], {})


if __name__ == '__main__':
    unittest.main()
