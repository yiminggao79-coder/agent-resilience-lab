import copy
import unittest
from api import slope
from differentiation import derivative
from parsing import parse


class SymbolicSensitivityTests(unittest.TestCase):
    def test_product_rule(self):
        self.assertEqual(slope('x*x', {'x': 3}, 'x'),
                         {'value': [9, 1], 'slope': [6, 1]})
        self.assertEqual(slope('x*y', {'x': [2, 3], 'y': 9}, 'x'),
                         {'value': [6, 1], 'slope': [9, 1]})

    def test_quotient_rule(self):
        self.assertEqual(slope('(x+1)/(x-1)', {'x': 2}, 'x'),
                         {'value': [3, 1], 'slope': [-2, 1]})
        self.assertEqual(slope('x/y', {'x': [2, 3], 'y': [-9, 2]}, 'x'),
                         {'value': [-4, 27], 'slope': [-2, 9]})

    def test_negative_powers_and_derived_exponents(self):
        self.assertEqual(slope('x^-2', {'x': 2}, 'x'),
                         {'value': [1, 4], 'slope': [-1, 4]})
        # The derivative exponent -13 is outside the parser's input limit.
        self.assertEqual(slope('x^-12', {'x': 2}, 'x'),
                         {'value': [1, 4096], 'slope': [-3, 2048]})
        self.assertEqual(slope('x^12', {'x': 2}, 'x'),
                         {'value': [4096, 1], 'slope': [24576, 1]})

    def test_unrelated_variable(self):
        self.assertEqual(slope('y^2', {'y': 3}, 'x'),
                         {'value': [9, 1], 'slope': [0, 1]})
        self.assertEqual(slope('2^3', {'y': 3}, '_unused'),
                         {'value': [8, 1], 'slope': [0, 1]})

    def test_original_is_evaluated_before_derivative(self):
        for text, values in (('1/0', {}), ('y-y', {}), ('(1-1)^-1', {})):
            with self.assertRaises(ValueError):
                slope(text, values, 'x')

    def test_invalid_variable_spelling(self):
        for variable in ('', 'bad name', '1x', 'a.b'):
            with self.assertRaises(ValueError):
                slope('x', {'x': 1}, variable)
            with self.assertRaises(ValueError):
                derivative(parse('x'), variable)

    def test_zero_power_derivative_is_directly_zero(self):
        tree = parse('x^0')
        self.assertEqual(derivative(tree, 'x'), ('number', 0))
        self.assertEqual(slope('(1-1)^0', {}, 'x'),
                         {'value': [1, 1], 'slope': [0, 1]})

    def test_existing_environment_validation_is_preserved(self):
        for values in ({'x': [1, 0]}, {'x': True}, {'x': [1]}, None):
            with self.assertRaises(ValueError):
                slope('x', values, 'x')

    def test_derivative_does_not_mutate_input_ast(self):
        tree = parse('x^2*y+1')
        before = copy.deepcopy(tree)
        derivative(tree, 'x')
        self.assertEqual(tree, before)


if __name__ == '__main__':
    unittest.main()
