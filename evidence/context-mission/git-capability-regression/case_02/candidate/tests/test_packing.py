import copy
import unittest

from boxes import validate as validate_boxes
from manifest import make, packed
from packing import pack

CATALOG = [
    {'sku': 'a', 'weight': 4, 'volume': 1},
    {'sku': 'b', 'weight': 4, 'volume': 1},
    {'sku': 'c', 'weight': 1, 'volume': 1},
]


class BoxValidationTests(unittest.TestCase):
    def test_boxes_are_sorted_by_volume_then_weight_then_code(self):
        rows = [
            {'code': 'z', 'max_weight': 5, 'max_volume': 5},
            {'code': 'a', 'max_weight': 5, 'max_volume': 5},
            {'code': 'b', 'max_weight': 9, 'max_volume': 2},
            {'code': 'c', 'max_weight': 4, 'max_volume': 5},
        ]
        self.assertEqual([box['code'] for box in validate_boxes(rows)], ['b', 'c', 'a', 'z'])

    def test_invalid_box_definitions_are_rejected_even_if_unused(self):
        cases = [
            [None],
            [{'code': 'x', 'max_weight': 1}],
            [{'code': 'x', 'max_volume': 1}],
            [{'code': ' x', 'max_weight': 1, 'max_volume': 1}],
            [{'code': '', 'max_weight': 1, 'max_volume': 1}],
            [{'code': 'x', 'max_weight': 0, 'max_volume': 1}],
            [{'code': 'x', 'max_weight': 1, 'max_volume': True}],
            [{'code': 'x', 'max_weight': True, 'max_volume': 1}],
            [{'code': 'x', 'max_weight': 1, 'max_volume': 1},
             {'code': 'x', 'max_weight': 2, 'max_volume': 2}],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                with self.assertRaises(ValueError):
                    validate_boxes(rows)

    def test_box_results_own_their_records(self):
        rows = [{'code': 'x', 'max_weight': 1, 'max_volume': 1}]
        result = validate_boxes(rows)
        result[0]['max_weight'] = 99
        self.assertEqual(rows[0]['max_weight'], 1)
        self.assertIsNot(result[0], rows[0])


class PackingTests(unittest.TestCase):
    def test_first_fit_reuses_earliest_created_carton(self):
        boxes = [{'code': 'big', 'max_weight': 5, 'max_volume': 10}]
        cartons = pack(CATALOG, [{'sku': 'a', 'quantity': 1},
                                 {'sku': 'b', 'quantity': 1},
                                 {'sku': 'c', 'quantity': 1}], boxes)
        self.assertEqual(cartons, [
            {'box': 'big', 'items': [{'sku': 'a', 'quantity': 1},
                                     {'sku': 'c', 'quantity': 1}],
             'weight': 5, 'volume': 2},
            {'box': 'big', 'items': [{'sku': 'b', 'quantity': 1}],
             'weight': 4, 'volume': 1},
        ])

    def test_volume_limit_forces_new_carton(self):
        catalog = [
            {'sku': 'd', 'weight': 2, 'volume': 4},
            {'sku': 'e', 'weight': 8, 'volume': 2},
        ]
        boxes = [{'code': 'box', 'max_weight': 10, 'max_volume': 5}]
        cartons = pack(catalog, [{'sku': 'd', 'quantity': 1},
                                 {'sku': 'e', 'quantity': 1}], boxes)
        self.assertEqual([carton['items'] for carton in cartons], [
            [{'sku': 'e', 'quantity': 1}],
            [{'sku': 'd', 'quantity': 1}],
        ])

    def test_weight_limit_forces_new_carton(self):
        catalog = [
            {'sku': 'a', 'weight': 4, 'volume': 1},
            {'sku': 'b', 'weight': 4, 'volume': 1},
        ]
        boxes = [{'code': 'box', 'max_weight': 5, 'max_volume': 100}]
        cartons = pack(catalog, [{'sku': 'a', 'quantity': 1},
                                 {'sku': 'b', 'quantity': 1}], boxes)
        self.assertEqual([carton['items'] for carton in cartons], [
            [{'sku': 'a', 'quantity': 1}],
            [{'sku': 'b', 'quantity': 1}],
        ])

    def test_repeated_order_lines_expand_and_split_into_units(self):
        catalog = [{'sku': 'a', 'weight': 2, 'volume': 1}]
        lines = [{'sku': 'a', 'quantity': 2}, {'sku': 'a', 'quantity': 3}]
        boxes = [{'code': 'pair', 'max_weight': 4, 'max_volume': 100}]
        self.assertEqual(pack(catalog, lines, boxes), [
            {'box': 'pair', 'items': [{'sku': 'a', 'quantity': 2}],
             'weight': 4, 'volume': 2},
            {'box': 'pair', 'items': [{'sku': 'a', 'quantity': 2}],
             'weight': 4, 'volume': 2},
            {'box': 'pair', 'items': [{'sku': 'a', 'quantity': 1}],
             'weight': 2, 'volume': 1},
        ])

    def test_code_breaks_deterministic_box_ties(self):
        catalog = [{'sku': 'a', 'weight': 1, 'volume': 1}]
        boxes = [
            {'code': 'z', 'max_weight': 5, 'max_volume': 5},
            {'code': 'a', 'max_weight': 5, 'max_volume': 5},
        ]
        self.assertEqual([box['code'] for box in validate_boxes(boxes)], ['a', 'z'])
        cartons = pack(catalog, [{'sku': 'a', 'quantity': 1}], boxes)
        self.assertEqual(cartons[0]['box'], 'a')

    def test_impossible_unit_raises(self):
        catalog = [{'sku': 'a', 'weight': 5, 'volume': 5}]
        boxes = [
            {'code': 'light', 'max_weight': 4, 'max_volume': 100},
            {'code': 'small', 'max_weight': 100, 'max_volume': 4},
        ]
        with self.assertRaises(ValueError):
            pack(catalog, [{'sku': 'a', 'quantity': 1}], boxes)

    def test_empty_boxes_are_legal_only_for_an_empty_order(self):
        self.assertEqual(pack(CATALOG, [], []), [])
        with self.assertRaises(ValueError):
            pack(CATALOG, [{'sku': 'a', 'quantity': 1}], [])

    def test_pack_does_not_mutate_or_share_inputs(self):
        catalog = copy.deepcopy(CATALOG)
        lines = [{'sku': 'a', 'quantity': 1}, {'sku': 'c', 'quantity': 1}]
        boxes = [{'code': 'big', 'max_weight': 5, 'max_volume': 10}]
        catalog_before = copy.deepcopy(catalog)
        lines_before = copy.deepcopy(lines)
        boxes_before = copy.deepcopy(boxes)
        cartons = pack(catalog, lines, boxes)
        self.assertEqual((catalog, lines, boxes), (catalog_before, lines_before, boxes_before))
        cartons[0]['items'][0]['quantity'] = 999
        self.assertEqual(lines, lines_before)


class ManifestPackedTests(unittest.TestCase):
    def test_packed_combines_report_and_cartons(self):
        boxes = [{'code': 'big', 'max_weight': 5, 'max_volume': 10}]
        lines = [{'sku': 'a', 'quantity': 1}, {'sku': 'b', 'quantity': 1}]
        result = packed(CATALOG, lines, boxes)
        expected_report = make(CATALOG, lines)
        self.assertEqual(result['items'], expected_report['items'])
        self.assertEqual(result['totals'], expected_report['totals'])
        self.assertEqual(result['cartons'], pack(CATALOG, lines, boxes))

    def test_packed_empty_order(self):
        self.assertEqual(packed(CATALOG, [], []), {
            'items': [],
            'totals': {'units': 0, 'weight': 0, 'volume': 0},
            'cartons': [],
        })


if __name__ == '__main__':
    unittest.main()
