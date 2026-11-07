import unittest
import copy
from reporting import report


class InvoiceTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(report([]), {'total': '0.00', 'quantity': 0, 'lines': 0})

    def test_decimal_half_up(self):
        self.assertEqual(report([{'unit_price': '0.105', 'qty': 1}]),
                         {'total': '0.11', 'quantity': 1, 'lines': 1})

    def test_input_is_not_modified(self):
        lines = [{'unit_price': ' 0.10 ', 'qty': 3, 'note': {'keep': True}}]
        before = copy.deepcopy(lines)
        report(lines)
        self.assertEqual(lines, before)
