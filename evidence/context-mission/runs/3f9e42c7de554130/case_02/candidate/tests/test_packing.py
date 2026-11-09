import copy
import unittest
from boxes import validate
from manifest import make, packed
from packing import pack

C=[{'sku':'a','weight':4,'volume':2},{'sku':'b','weight':2,'volume':3}]

def box(code='big',max_weight=10,max_volume=10):
    return {'code':code,'max_weight':max_weight,'max_volume':max_volume}

class PackingRegressions(unittest.TestCase):
    def test_first_fit_reuse_in_creation_order(self):
        cartons=pack(C,[{'sku':'a','quantity':3},{'sku':'b','quantity':1}],[box()])
        self.assertEqual(cartons,[
            {'box':'big','items':[{'sku':'a','quantity':2},{'sku':'b','quantity':1}],
             'weight':10,'volume':7},
            {'box':'big','items':[{'sku':'a','quantity':1}],'weight':4,'volume':2}])

    def test_volume_and_weight_limits_are_independent(self):
        volumes=[{'sku':'a','weight':3,'volume':3},{'sku':'b','weight':3,'volume':3}]
        volume_cartons=pack(volumes,[{'sku':'a','quantity':1},{'sku':'b','quantity':1}],
                             [box('weighty',max_weight=100,max_volume=5)])
        self.assertEqual(volume_cartons,[
            {'box':'weighty','items':[{'sku':'a','quantity':1}],'weight':3,'volume':3},
            {'box':'weighty','items':[{'sku':'b','quantity':1}],'weight':3,'volume':3}])
        weights=[{'sku':'a','weight':4,'volume':1},{'sku':'b','weight':4,'volume':1}]
        weight_cartons=pack(weights,[{'sku':'a','quantity':1},{'sku':'b','quantity':1}],
                             [box('light',max_weight=5,max_volume=100)])
        self.assertEqual(weight_cartons,[
            {'box':'light','items':[{'sku':'a','quantity':1}],'weight':4,'volume':1},
            {'box':'light','items':[{'sku':'b','quantity':1}],'weight':4,'volume':1}])

    def test_first_fit_scans_all_existing_cartons(self):
        catalog=[{'sku':s,'weight':w,'volume':1} for s,w in [('a',3),('b',3),('c',2),('d',1)]]
        cartons=pack(catalog,[{'sku':'a','quantity':1},{'sku':'b','quantity':1},
                              {'sku':'c','quantity':1},{'sku':'d','quantity':1}],
                     [box(max_weight=4,max_volume=100)])
        self.assertEqual([[i['sku'] for i in c['items']] for c in cartons],
                         [['a','d'],['b'],['c']])

    def test_repeated_units_are_indivisible(self):
        catalog=[{'sku':'a','weight':1,'volume':1}]
        cartons=pack(catalog,[{'sku':'a','quantity':3}],[box(max_weight=2,max_volume=2)])
        self.assertEqual(cartons,[
            {'box':'big','items':[{'sku':'a','quantity':2}],'weight':2,'volume':2},
            {'box':'big','items':[{'sku':'a','quantity':1}],'weight':1,'volume':1}])

    def test_unit_processing_order(self):
        catalog=[{'sku':'a','weight':1,'volume':1},{'sku':'b','weight':2,'volume':1},
                 {'sku':'c','weight':2,'volume':2}]
        cartons=pack(catalog,[{'sku':'a','quantity':1},{'sku':'b','quantity':1},
                              {'sku':'c','quantity':1}],[box(max_weight=2,max_volume=2)])
        self.assertEqual([[i['sku'] for i in c['items']] for c in cartons],[['c'],['b'],['a']])

    def test_deterministic_box_ties(self):
        boxes=[box('z',10,10),box('a',10,10),box('m',5,10),box('b',100,5)]
        self.assertEqual([b['code'] for b in validate(boxes)],['b','m','a','z'])
        catalog=[{'sku':'a','weight':6,'volume':6}]
        cartons=pack(catalog,[{'sku':'a','quantity':1}],boxes)
        self.assertEqual(cartons[0]['box'],'a')

    def test_impossible_units(self):
        catalog=[{'sku':'a','weight':5,'volume':5}]
        lines=[{'sku':'a','quantity':1}]
        for boxes in [[],[box(max_weight=4,max_volume=100)],
                      [box(max_weight=100,max_volume=4)]]:
            with self.assertRaises(ValueError):
                pack(catalog,lines,boxes)

    def test_empty_order_and_empty_box_list(self):
        self.assertEqual(pack(C,[],[]),[])
        self.assertEqual(packed(C,[],[]),{'items':[],'totals':{'units':0,'weight':0,'volume':0},
                                          'cartons':[]})

    def test_all_box_definitions_validated(self):
        lines=[{'sku':'a','quantity':1}]
        invalid=[
            [box('big'),{'code':'big','max_weight':1,'max_volume':1}],
            [{'code':'x','max_weight':True,'max_volume':1}],
            [{'code':'x','max_weight':1,'max_volume':False}],
            [{'code':'x','max_weight':0,'max_volume':1}],
            [{'code':'x','max_weight':1,'max_volume':-1}],
            [{'code':' x','max_weight':1,'max_volume':1}],
            [{'code':'','max_weight':1,'max_volume':1}],
            [{'code':'x','max_volume':1}],
            ['not-a-dict'],
            'not-a-list',
        ]
        for boxes in invalid:
            with self.assertRaises(ValueError):
                pack(C,lines,boxes)
            with self.assertRaises(ValueError):
                pack(C,[],boxes)

    def test_packed_includes_existing_report_and_cartons(self):
        lines=[{'sku':'a','quantity':2},{'sku':'b','quantity':1}]
        boxes=[box()]
        report=packed(C,lines,boxes)
        self.assertEqual(report['items'],make(C,lines)['items'])
        self.assertEqual(report['totals'],make(C,lines)['totals'])
        self.assertEqual(report['cartons'],pack(C,lines,boxes))

    def test_no_mutation_or_shared_records(self):
        catalog=copy.deepcopy(C)
        lines=[{'sku':'a','quantity':2},{'sku':'b','quantity':1}]
        boxes=[box()]
        before=(copy.deepcopy(catalog),copy.deepcopy(lines),copy.deepcopy(boxes))
        first=packed(catalog,lines,boxes)
        self.assertEqual((catalog,lines,boxes),before)
        first['items'][0]['quantity']=999
        first['cartons'][0]['items'][0]['quantity']=999
        second=packed(catalog,lines,boxes)
        self.assertEqual(second['items'],make(C,lines)['items'])
        self.assertEqual(second['cartons'],pack(C,lines,boxes))

if __name__=='__main__':
    unittest.main()
