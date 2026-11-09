import copy
import unittest
from api import calculate
from parsing import parse
from rendering import render
from evaluation import evaluate,pair


class UnaryPowerPrecedence(unittest.TestCase):
    def test_power_binds_atom_before_outer_sign(self):
        self.assertEqual(calculate('-2^2',{}),{'canonical':'(-(2^2))','value':[-4,1]})
        self.assertEqual(calculate('(-2)^2',{})['value'],[4,1])
        self.assertEqual(calculate('(-2)^2',{})['canonical'],'((-2)^2)')

    def test_repeated_signs(self):
        self.assertEqual(calculate('--2^2',{}),{'canonical':'(-(-(2^2)))','value':[4,1]})
        self.assertEqual(calculate('---2',{})['value'],[-2,1])
        self.assertEqual(calculate('+2^2',{})['value'],[4,1])
        self.assertEqual(calculate('-+2^2',{})['value'],[-4,1])

    def test_signs_before_parentheses(self):
        self.assertEqual(calculate('-(2+3)',{})['value'],[-5,1])
        self.assertEqual(calculate('-(-2)^2',{})['value'],[-4,1])
        self.assertEqual(calculate('-(x+1)^2',{'x':2})['value'],[-9,1])

    def test_power_suffix_sign(self):
        self.assertEqual(calculate('2^-2',{}),{'canonical':'(2^-2)','value':[1,4]})
        self.assertEqual(calculate('2^+2',{})['value'],[4,1])
        self.assertEqual(calculate('-2^-2',{})['value'],[-1,4])
        self.assertEqual(calculate('(-2)^-2',{})['value'],[1,4])


class ExactRationals(unittest.TestCase):
    def test_division_is_exact(self):
        self.assertEqual(calculate('1/2+1/3',{})['value'],[5,6])
        self.assertEqual(calculate('1/3+1/6',{})['value'],[1,2])
        self.assertEqual(calculate('-1/3',{})['value'],[-1,3])
        self.assertEqual(calculate('1/2*3',{})['value'],[3,2])
        self.assertEqual(calculate('x/2',{'x':[2,3]})['value'],[1,3])
        self.assertEqual(calculate('1/x',{'x':[2,3]})['value'],[3,2])

    def test_left_associativity(self):
        self.assertEqual(calculate('8/4/2',{})['value'],[1,1])
        self.assertEqual(calculate('1/2/3',{})['value'],[1,6])
        self.assertEqual(calculate('8/4*2',{})['value'],[4,1])
        self.assertEqual(calculate('1-2-3',{})['value'],[-4,1])

    def test_multiplicative_binds_tighter_than_additive(self):
        self.assertEqual(calculate('1+2*3',{})['value'],[7,1])
        self.assertEqual(calculate('2*3^2',{})['value'],[18,1])
        self.assertEqual(calculate('-2^2*3',{})['value'],[-12,1])
        self.assertEqual(calculate('2^-1*4',{})['value'],[2,1])

    def test_zero_powers(self):
        self.assertEqual(calculate('0^0',{}),{'canonical':'(0^0)','value':[1,1]})
        self.assertEqual(calculate('(1-1)^0',{}),{'canonical':'((1-1)^0)','value':[1,1]})
        self.assertEqual(calculate('0^2',{})['value'],[0,1])
        with self.assertRaises(ValueError):
            calculate('0^-1',{})

    def test_division_by_zero(self):
        for text in ('1/0','1/(-0)','0/0'):
            with self.assertRaises(ValueError):
                calculate(text,{})


class Validation(unittest.TestCase):
    def test_exponent_grammar_unchanged(self):
        for text in ('2^2^2','2^(2)','2^x','x^2^3','2^13','2^-13','2^+13',
                     '2^','2^^2','2^--2','^2'):
            with self.assertRaises(ValueError):
                parse(text)

    def test_environment_validation(self):
        for values in ({'x':True},{'x':[1,0]},{'x':[1]},{'x':1.5},
                       {'x':[True,2]},{'x':'2'},{'1x':2},[('x',1)],'x'):
            with self.assertRaises(ValueError):
                evaluate(parse('x'),values)

    def test_unbound_variable(self):
        with self.assertRaises(ValueError):
            calculate('x+1',{})

    def test_caller_inputs_preserved(self):
        values={'x':[1,2],'y':[-3,4]}
        before=copy.deepcopy(values)
        calculate('-x^2+y/2',values)
        self.assertEqual(values,before)
        tree=parse('-x^2+y/2')
        tree_before=copy.deepcopy(tree)
        evaluate(tree,values)
        self.assertEqual(tree,tree_before)


if __name__=='__main__':
    unittest.main()
