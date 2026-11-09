import copy
import unittest

from catalog import products
from manifest import make
from orders import quantities

CATALOG = [
    {'sku': 'a', 'weight': 4, 'volume': 2},
    {'sku': 'b', 'weight': 2, 'volume': 3},
]


class AggregationRegressions(unittest.TestCase):
    def test_repeated_lines_add_before_measuring(self):
        lines = [
            {'sku': 'a', 'quantity': 2},
            {'sku': 'b', 'quantity': 1},
            {'sku': 'a', 'quantity': 3},
        ]
        self.assertEqual(quantities(lines, products(CATALOG)), {'a': 5, 'b': 1})
        result = make(CATALOG, lines)
        self.assertEqual(result['items'], [
            {'sku': 'a', 'quantity': 5, 'weight': 20, 'volume': 10},
            {'sku': 'b', 'quantity': 1, 'weight': 2, 'volume': 3},
        ])
        self.assertEqual(result['totals'], {'units': 6, 'weight': 22, 'volume': 13})

    def test_units_are_quantities_not_distinct_skus(self):
        result = make(CATALOG, [{'sku': 'a', 'quantity': 2}, {'sku': 'a', 'quantity': 3}])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['totals']['units'], 5)

    def test_empty_catalog_and_order(self):
        self.assertEqual(products([]), {})
        self.assertEqual(make([], []), {'items': [], 'totals': {'units': 0, 'weight': 0, 'volume': 0}})
        self.assertEqual(make(CATALOG, []), {'items': [], 'totals': {'units': 0, 'weight': 0, 'volume': 0}})

    def test_item_fields_are_exact_and_sorted(self):
        result = make(CATALOG, [{'sku': 'b', 'quantity': 1}, {'sku': 'a', 'quantity': 1}])
        self.assertEqual([item['sku'] for item in result['items']], ['a', 'b'])
        for item in result['items']:
            self.assertEqual(set(item), {'sku', 'quantity', 'weight', 'volume'})

    def test_case_sensitive_skus(self):
        catalog = [
            {'sku': 'A', 'weight': 1, 'volume': 1},
            {'sku': 'a', 'weight': 2, 'volume': 2},
        ]
        result = make(catalog, [{'sku': 'A', 'quantity': 1}, {'sku': 'a', 'quantity': 2}])
        self.assertEqual([item['sku'] for item in result['items']], ['A', 'a'])
        self.assertEqual(result['totals']['units'], 3)


class ValidationRegressions(unittest.TestCase):
    def test_invalid_unused_catalog_product_is_rejected(self):
        invalid_catalog = CATALOG + [{'sku': 'unused', 'weight': 0, 'volume': 1}]
        with self.assertRaises(ValueError):
            make(invalid_catalog, [{'sku': 'a', 'quantity': 1}])

    def test_malformed_catalog_records_are_rejected(self):
        cases = [
            [None],
            [{'sku': 'x', 'weight': 1}],
            [{'sku': 'x', 'volume': 1}],
            [{'sku': ' x', 'weight': 1, 'volume': 1}],
            [{'sku': '', 'weight': 1, 'volume': 1}],
            [{'sku': 'x', 'weight': True, 'volume': 1}],
            [{'sku': 'x', 'weight': 1, 'volume': False}],
            [{'sku': 'x', 'weight': -1, 'volume': 1}],
        ]
        for catalog in cases:
            with self.subTest(catalog=catalog):
                with self.assertRaises(ValueError):
                    products(catalog)

    def test_duplicate_catalog_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            products(CATALOG + [dict(CATALOG[0])])

    def test_unknown_and_malformed_order_records_are_rejected(self):
        cases = [
            [None],
            [{'sku': 'a'}],
            [{'quantity': 1}],
            [{'sku': 'a', 'quantity': 0}],
            [{'sku': 'a', 'quantity': True}],
            [{'sku': 'missing', 'quantity': 1}],
        ]
        for lines in cases:
            with self.subTest(lines=lines):
                with self.assertRaises(ValueError):
                    make(CATALOG, lines)

    def test_missing_and_nonpositive_product_fields_are_rejected(self):
        cases = [
            [{'weight': 1, 'volume': 1}],
            [{'sku': 'x', 'volume': 1}],
            [{'sku': 'x', 'weight': 1}],
            [{'sku': 'x', 'weight': 0, 'volume': 1}],
            [{'sku': 'x', 'weight': 1, 'volume': 0}],
        ]
        for catalog in cases:
            with self.subTest(catalog=catalog):
                with self.assertRaises(ValueError):
                    products(catalog)


class OwnershipRegressions(unittest.TestCase):
    def test_inputs_are_not_mutated(self):
        catalog = copy.deepcopy(CATALOG)
        lines = [
            {'sku': 'a', 'quantity': 2},
            {'sku': 'a', 'quantity': 3},
            {'sku': 'b', 'quantity': 1},
        ]
        catalog_before, lines_before = copy.deepcopy(catalog), copy.deepcopy(lines)
        result = make(catalog, lines)
        self.assertEqual(catalog, catalog_before)
        self.assertEqual(lines, lines_before)
        result['items'][0]['weight'] = 999
        self.assertEqual(catalog, catalog_before)

    def test_product_results_are_owned_copies(self):
        result = products(CATALOG)
        result['a']['weight'] = 999
        self.assertEqual(CATALOG[0]['weight'], 4)
        self.assertIsNot(result['a'], CATALOG[0])


if __name__ == '__main__':
    unittest.main()
