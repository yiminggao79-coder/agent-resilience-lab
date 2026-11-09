import copy
import unittest
from api import workbook_report
from workbook import run


class WorkbookTests(unittest.TestCase):
    def test_chain_shared_dependencies_and_requested_input(self):
        formulas={'a':'x*y','b':'a+x','c':'a*b'}
        result=run(formulas,{'x':2,'y':3},['x','c','a'],'x')
        self.assertEqual(result['order'],['a','b','c'])
        self.assertEqual(result['results'],[
            {'name':'x','value':[2,1],'derivative':[1,1]},
            {'name':'c','value':[48,1],'derivative':[48,1]},
            {'name':'a','value':[6,1],'derivative':[3,1]},
        ])

    def test_deterministic_lexicographic_ready_order(self):
        result=run({'z':'x+1','a':'y+1','m':'x+y'},
                   {'x':1,'y':2},['z','m','a'],'x')
        self.assertEqual(result['order'],['a','m','z'])
        self.assertEqual(result['results'],[
            {'name':'z','value':[2,1],'derivative':[1,1]},
            {'name':'m','value':[3,1],'derivative':[1,1]},
            {'name':'a','value':[3,1],'derivative':[0,1]},
        ])

    def test_report_expressions_are_sorted_and_reduced(self):
        result=workbook_report({'b':'x+0','a':'1/2'},
                               {'x':4},['b','a'],'x')
        self.assertEqual(result['order'],['a','b'])
        self.assertEqual(result['results'],[
            {'name':'b','value':[4,1],'derivative':[1,1]},
            {'name':'a','value':[1,2],'derivative':[0,1]},
        ])
        self.assertEqual(list(result['expressions']),['a','b'])
        self.assertEqual(result['expressions'],{'a':'(1/2)','b':'x'})

    def test_negative_power_chain_is_exact(self):
        result=run({'a':'x^-2','b':'1/a'},{'x':2},['b'],'x')
        self.assertEqual(result['order'],['a','b'])
        self.assertEqual(result['results'],[
            {'name':'b','value':[4,1],'derivative':[4,1]},
        ])

    def test_cycles_are_rejected_even_when_unused(self):
        with self.assertRaises(ValueError):
            run({'a':'b','b':'a','good':'x'},
                {'x':1},['good'],'x')

    def test_unknown_names_are_rejected_even_when_unused(self):
        with self.assertRaises(ValueError):
            run({'a':'missing','good':'x'},{'x':1},['good'],'x')

    def test_unused_undefined_formula_still_raises(self):
        with self.assertRaises(ValueError):
            run({'bad':'1/(x-x)','good':'x'},{'x':1},['good'],'x')

    def test_structure_validation(self):
        with self.assertRaises(ValueError):
            run({'x':'1'},{'x':1},[],'x')
        with self.assertRaises(ValueError):
            run({'a':'x'},{'x':1},'a','x')
        with self.assertRaises(ValueError):
            run({'a':'x'},{'x':1},['a','a'],'x')
        with self.assertRaises(ValueError):
            run({'a':'x'},{'x':1},['missing'],'x')
        with self.assertRaises(ValueError):
            run({'a':'x'},{'x':1},['a'],'a')

    def test_inputs_are_unchanged_on_success_and_failure(self):
        inputs={'x':[1,3],'y':[2,5]}
        before=copy.deepcopy(inputs)
        result=run({'a':'x+y'},inputs,['a'],'x')
        self.assertEqual(result['results'],[
            {'name':'a','value':[11,15],'derivative':[1,1]},
        ])
        self.assertEqual(inputs,before)
        with self.assertRaises(ValueError):
            run({'bad':'1/(x-x)','good':'y'},inputs,['good'],'x')
        self.assertEqual(inputs,before)


if __name__=='__main__':
    unittest.main()
