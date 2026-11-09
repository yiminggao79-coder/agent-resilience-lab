import copy
import unittest
from catalog import products
from orders import quantities
from manifest import make

C=[{'sku':'a','weight':4,'volume':2},{'sku':'b','weight':2,'volume':3}]

class AggregationRegressions(unittest.TestCase):
    def test_repeated_lines_accumulate(self):
        self.assertEqual(quantities([{'sku':'a','quantity':2},{'sku':'a','quantity':3},
                                     {'sku':'b','quantity':1}],products(C)),{'a':5,'b':1})
    def test_units_sum_quantities_not_distinct_skus(self):
        report=make(C,[{'sku':'a','quantity':2},{'sku':'a','quantity':3}])
        self.assertEqual(report['totals'],{'units':5,'weight':20,'volume':10})
        self.assertEqual([i['sku'] for i in report['items']],['a'])
    def test_item_measure_totals(self):
        report=make(C,[{'sku':'a','quantity':2},{'sku':'a','quantity':1},
                       {'sku':'b','quantity':2}])
        self.assertEqual(report['items'],[
            {'sku':'a','quantity':3,'weight':12,'volume':6},
            {'sku':'b','quantity':2,'weight':4,'volume':6}])
        self.assertEqual(report['totals'],{'units':5,'weight':16,'volume':12})
    def test_empty_catalog_and_lines(self):
        self.assertEqual(make([],[]),{'items':[],'totals':{'units':0,'weight':0,'volume':0}})
        self.assertEqual(products([]),{})
        self.assertEqual(quantities([],products(C)),{})
    def test_unused_catalog_products_validated(self):
        with self.assertRaises(ValueError):
            make(C+[{'sku':'c','weight':0,'volume':1}],[{'sku':'a','quantity':1}])
    def test_no_mutation(self):
        catalog=copy.deepcopy(C)
        lines=[{'sku':'a','quantity':2},{'sku':'a','quantity':3},{'sku':'b','quantity':1}]
        catalog_before=copy.deepcopy(catalog)
        lines_before=copy.deepcopy(lines)
        make(catalog,lines)
        self.assertEqual(catalog,catalog_before)
        self.assertEqual(lines,lines_before)
    def test_unknown_sku_in_repeated_lines(self):
        with self.assertRaises(ValueError):
            make(C,[{'sku':'a','quantity':1},{'sku':'z','quantity':2}])
    def test_invalid_records(self):
        for lines in [[{'quantity':1}],[{'sku':'a'}],[{'sku':'','quantity':1}]]:
            with self.assertRaises(ValueError):
                quantities(lines,products(C))
        with self.assertRaises(ValueError):
            quantities('not-a-list',products(C))
        for row in [{'sku':'x','volume':1},{'sku':'x','weight':1},
                    {'sku':'x','weight':True,'volume':1},'bad']:
            with self.assertRaises(ValueError):
                products([row])

if __name__=='__main__':
    unittest.main()
