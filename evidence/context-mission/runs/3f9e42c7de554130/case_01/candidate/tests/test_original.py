import copy
import unittest
from api import calculate
from parsing import parse
from rendering import render
class OriginalContracts(unittest.TestCase):
    def test_exact_division(self):
        self.assertEqual(calculate('1/2+1/3',{})['value'],[5,6])
    def test_power_before_unary(self):
        self.assertEqual(calculate('-2^2',{}),{'canonical':'(-(2^2))','value':[-4,1]})
    def test_parenthesized_negative(self):
        self.assertEqual(calculate('(-2)^2',{})['value'],[4,1])
    def test_left_associativity(self):
        self.assertEqual(calculate('8/4/2',{})['value'],[1,1])
    def test_negative_exponent(self):
        self.assertEqual(calculate('2^-2',{})['value'],[1,4])
    def test_variables(self):
        self.assertEqual(calculate('x*y',{'x':[2,3],'y':9})['value'],[6,1])
    def test_owned_inputs(self):
        env={'x':[1,2]}
        before=copy.deepcopy(env)
        calculate('x+1',env)
        self.assertEqual(env,before)
    def test_unbound_and_zero(self):
        for text in ['x','1/0','0^-1']:
            with self.assertRaises(ValueError): calculate(text,{})
    def test_invalid_syntax(self):
        for text in ['', '2^13','x^y','1+','a.b','2^2^2']:
            with self.assertRaises(ValueError): parse(text)
    def test_render(self):
        self.assertEqual(render(parse('a*(b+3)')),'(a*(b+3))')
if __name__=='__main__': unittest.main()
