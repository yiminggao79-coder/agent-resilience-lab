import copy
import unittest
from catalog import products
from orders import quantities
from manifest import make
C=[{'sku':'a','weight':4,'volume':2},{'sku':'b','weight':2,'volume':3}]
class OriginalContracts(unittest.TestCase):
    def test_duplicate_lines_add(self):
        self.assertEqual(quantities([{'sku':'a','quantity':2},{'sku':'a','quantity':3}],products(C)),{'a':5})
    def test_units_count(self):
        self.assertEqual(make(C,[{'sku':'a','quantity':3}])['totals']['units'],3)
    def test_measure_totals(self):
        self.assertEqual(make(C,[{'sku':'a','quantity':2},{'sku':'b','quantity':1}])['totals'],
                         {'units':3,'weight':10,'volume':7})
    def test_sorted_items(self):
        self.assertEqual([v['sku'] for v in make(C,[{'sku':'b','quantity':1},{'sku':'a','quantity':1}])['items']],['a','b'])
    def test_empty_order(self):
        self.assertEqual(make(C,[]),{'items':[],'totals':{'units':0,'weight':0,'volume':0}})
    def test_catalog_copy(self):
        before=copy.deepcopy(C)
        products(C)['a']['weight']=999
        self.assertEqual(C,before)
    def test_duplicate_catalog(self):
        with self.assertRaises(ValueError): products(C+[C[0]])
    def test_unknown_sku(self):
        with self.assertRaises(ValueError): make(C,[{'sku':'z','quantity':1}])
    def test_invalid_quantities(self):
        for quantity in [0,-1,True]:
            with self.assertRaises(ValueError): make(C,[{'sku':'a','quantity':quantity}])
    def test_invalid_measures(self):
        for weight in [0,True,-1]:
            with self.assertRaises(ValueError): products([{'sku':'x','weight':weight,'volume':1}])
if __name__=='__main__': unittest.main()
