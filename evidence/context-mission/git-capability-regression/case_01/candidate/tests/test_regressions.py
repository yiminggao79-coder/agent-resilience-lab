import copy
import unittest
from api import calculate
from parsing import parse
from rendering import render


class PrecedenceRegressionTests(unittest.TestCase):
    def test_power_binds_to_atom_before_unary_sign(self):
        self.assertEqual(calculate('-2^2', {}), {'canonical': '(-(2^2))', 'value': [-4, 1]})
        self.assertEqual(calculate('(-2)^2', {})['value'], [4, 1])
        self.assertEqual(calculate('-x^2', {'x': -3})['value'], [-9, 1])
        self.assertEqual(calculate('-(2)^2', {})['value'], [-4, 1])

    def test_repeated_unary_signs(self):
        self.assertEqual(calculate('--2^2', {})['value'], [4, 1])
        self.assertEqual(calculate('+-2^2', {})['value'], [-4, 1])
        self.assertEqual(calculate('2*-3', {})['value'], [-6, 1])
        self.assertEqual(calculate('--(1+2)', {})['value'], [3, 1])

    def test_additive_and_multiplicative_left_associativity(self):
        self.assertEqual(calculate('1-2-3', {})['value'], [-4, 1])
        self.assertEqual(calculate('8-3+2', {})['value'], [7, 1])
        self.assertEqual(calculate('8/4/2', {})['value'], [1, 1])
        self.assertEqual(calculate('8/4*2', {})['value'], [4, 1])
        self.assertEqual(calculate('12/2/3', {})['value'], [2, 1])


class ExactArithmeticRegressionTests(unittest.TestCase):
    def test_division_is_exact_rational_arithmetic(self):
        self.assertEqual(calculate('1/2+1/3', {})['value'], [5, 6])
        self.assertEqual(calculate('1/3*3', {})['value'], [1, 1])
        self.assertEqual(calculate('1/(2/3)', {})['value'], [3, 2])
        self.assertEqual(calculate('-1/2', {})['value'], [-1, 2])

    def test_power_bounds_and_signed_literal_exponents(self):
        self.assertEqual(calculate('2^+2', {})['value'], [4, 1])
        self.assertEqual(calculate('2^-2', {})['value'], [1, 4])
        self.assertEqual(calculate('2^12', {})['value'], [4096, 1])
        self.assertEqual(calculate('2^-12', {})['value'], [1, 4096])
        for text in ('2^13', '2^-13', '2^2^2', '2^x', '2^(2)', '2^-x', '2^--2'):
            with self.assertRaises(ValueError):
                parse(text)

    def test_zero_powers(self):
        self.assertEqual(calculate('0^0', {})['value'], [1, 1])
        self.assertEqual(calculate('(1-1)^0', {})['value'], [1, 1])
        with self.assertRaises(ValueError):
            calculate('0^-1', {})
        with self.assertRaises(ValueError):
            calculate('(1-1)^-2', {})

    def test_invalid_environments_and_values(self):
        for values in (None, [], {'x': True}, {'x': [True, 1]}, {'x': [1, True]},
                       {'x': [1, 0]}, {'x': [1]}, {'x': 1.5}, {'bad name': 1}):
            with self.assertRaises(ValueError):
                calculate('x', values)
        with self.assertRaises(ValueError):
            calculate('x', {})

    def test_division_by_zero(self):
        with self.assertRaises(ValueError):
            calculate('1/(1-1)', {})

    def test_public_api_preserves_nested_input(self):
        values = {'x': [1, 3], 'y': [1, 6]}
        before = copy.deepcopy(values)
        result = calculate('x+y', values)
        self.assertEqual(result['value'], [1, 2])
        self.assertEqual(values, before)

    def test_canonical_rendering(self):
        self.assertEqual(render(parse('a*(b+3)')), '(a*(b+3))')
        self.assertEqual(render(parse('-2^2')), '(-(2^2))')
        self.assertEqual(render(parse('(-2)^2')), '((-2)^2)')


if __name__ == '__main__':
    unittest.main()
