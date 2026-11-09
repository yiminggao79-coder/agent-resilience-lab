import copy
import unittest
from portions import apportion
from ledger import balances, records
from views import statement

def expense(identity='e', payer='a', amount=10, weights=None):
    return dict(id=identity, payer=payer, amount=amount,
                weights={'a': 1, 'b': 1, 'c': 1} if weights is None else weights)

class OriginalContracts(unittest.TestCase):
    def test_conserves_cents(self):
        self.assertEqual(apportion(10, {'a': 1, 'b': 1, 'c': 1}), {'a': 4, 'b': 3, 'c': 3})
    def test_largest_remainder(self):
        self.assertEqual(apportion(7, {'a': 1, 'b': 3}), {'a': 2, 'b': 5})
    def test_negative_symmetry(self):
        self.assertEqual(apportion(-5, {'a': 1, 'b': 2}), {'a': -2, 'b': -3})
    def test_name_breaks_ties(self):
        self.assertEqual(apportion(1, {'z': 1, 'a': 1}), {'a': 1, 'z': 0})
    def test_zero_weight_stays_zero(self):
        self.assertEqual(apportion(2, {'a': 0, 'b': 1}), {'a': 0, 'b': 2})
    def test_validation(self):
        for total, weights in [(True, {'a': 1}), (3, {'a': 0}), (3, {'a': -1}), (3, {' a': 1})]:
            with self.assertRaises(ValueError):
                apportion(total, weights)
    def test_balances_sum_to_zero(self):
        self.assertEqual(balances([expense()]), {'a': 6, 'b': -3, 'c': -3})
    def test_owned_nested_records(self):
        data = [expense()]
        before = copy.deepcopy(data)
        records(data)[0]['weights']['a'] = 99
        self.assertEqual(data, before)
    def test_duplicate_expenses(self):
        with self.assertRaises(ValueError):
            balances([expense(), expense()])
    def test_statement(self):
        self.assertEqual(statement([expense(payer='x', amount=6, weights={'b': 1})]),
            {'balances': {'b': -6, 'x': 6}, 'paid': {'x': 6}, 'spent': 6})

if __name__ == '__main__':
    unittest.main()
