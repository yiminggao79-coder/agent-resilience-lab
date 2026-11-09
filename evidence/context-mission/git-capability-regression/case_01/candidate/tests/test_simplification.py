import copy
import unittest
from api import reduced
from nodes import number, variable
from parsing import parse
from rendering import render
from simplification import reduce


class SimplificationTests(unittest.TestCase):
    def test_exact_fractional_constants(self):
        self.assertEqual(reduce(parse('1/2+1/3')),
                         ('div', number(5), number(6)))
        self.assertEqual(reduce(parse('2^-2')),
                         ('div', number(1), number(4)))
        self.assertEqual(reduce(parse('6/3')), number(2))
        self.assertEqual(reduce(parse('1/2-1')),
                         ('div', number(-1), number(2)))
        self.assertEqual(reduce(parse('0^0')), number(1))
        self.assertEqual(render(reduce(parse('1/2+1/3'))), '(5/6)')

    def test_only_listed_symbolic_identities(self):
        identities = {
            '0+x': 'x',
            'x+0': 'x',
            'x-0': 'x',
            '1*x': 'x',
            'x*1': 'x',
            'x/1': 'x',
            'x^1': 'x',
        }
        for expression, expected in identities.items():
            with self.subTest(expression=expression):
                self.assertEqual(render(reduce(parse(expression))), expected)

    def test_unsafe_rewrites_are_retained(self):
        self.assertEqual(render(reduce(parse('0*x'))), '(0*x)')
        self.assertEqual(render(reduce(parse('x*0'))), '(x*0)')
        self.assertEqual(render(reduce(parse('x^0'))), '(x^0)')
        self.assertEqual(render(reduce(parse('x-x'))), '(x-x)')
        self.assertEqual(render(reduce(parse('0-x'))), '(0-x)')

    def test_double_negation_is_removed(self):
        self.assertEqual(reduce(parse('--x')), variable('x'))
        self.assertEqual(render(reduce(parse('---x'))), '(-x)')
        self.assertEqual(reduce(parse('---5')), number(-5))

    def test_invalid_constant_operations_raise(self):
        for expression in ('1/0', '0^-1', '1/(2-2)', '(1/0)*0'):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    reduce(parse(expression))

    def test_reduced_report(self):
        self.assertEqual(
            reduced('z+1*x+y', {'x': 2, 'y': 3, 'z': 4}, 'x'),
            {'canonical': '((z+x)+y)', 'variables': ['x', 'y', 'z'],
             'value': [9, 1], 'slope': [1, 1]})
        self.assertEqual(
            reduced('1/2+1/3', {}, 'x'),
            {'canonical': '(5/6)', 'variables': [],
             'value': [5, 6], 'slope': [0, 1]})
        self.assertEqual(
            reduced('--x', {'x': [1, 2]}, 'x'),
            {'canonical': 'x', 'variables': ['x'],
             'value': [1, 2], 'slope': [1, 1]})

    def test_original_domain_errors_are_preserved(self):
        for expression in ('1/0', '0^-1', 'y-y', '0*(1/0)'):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    reduced(expression, {}, 'x')

    def test_inputs_and_ast_are_not_mutated(self):
        tree = parse('0+x^0')
        before_tree = copy.deepcopy(tree)
        values = {'x': [1, 2]}
        before_values = copy.deepcopy(values)
        reduce(tree)
        reduced('0+x^0', values, 'x')
        self.assertEqual(tree, before_tree)
        self.assertEqual(values, before_values)


if __name__ == '__main__':
    unittest.main()
