"""Regression tests for reporting.report and the money/parsing modules."""

import copy
import os
import unittest
from decimal import Decimal

from money import money
from parsing import InvalidLine, parse_line, parse_lines
from pricing import total, total_for
from reporting import report

ERROR = {"error": "invalid_input"}


class ReportValidInputTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(report([]), {"total": "0.00", "quantity": 0, "lines": 0})

    def test_sums_quantity_and_lines(self):
        lines = [
            {"unit_price": "1.25", "qty": 2},
            {"unit_price": "0.10", "qty": 3},
        ]
        self.assertEqual(
            report(lines), {"total": "2.80", "quantity": 5, "lines": 2}
        )

    def test_whitespace_around_price_is_allowed(self):
        self.assertEqual(
            report([{"unit_price": " 2.50\n", "qty": 1}]),
            {"total": "2.50", "quantity": 1, "lines": 1},
        )

    def test_unrelated_fields_are_ignored(self):
        line = {"unit_price": "1.00", "qty": 1, "note": {"keep": True}, "id": None}
        self.assertEqual(report([line]), {"total": "1.00", "quantity": 1, "lines": 1})

    def test_zero_quantity_and_zero_price(self):
        self.assertEqual(
            report([{"unit_price": "0", "qty": 0}]),
            {"total": "0.00", "quantity": 0, "lines": 1},
        )

    def test_always_two_places(self):
        self.assertEqual(
            report([{"unit_price": "10", "qty": 1}]),
            {"total": "10.00", "quantity": 1, "lines": 1},
        )

    def test_input_is_not_mutated(self):
        lines = [{"unit_price": " 0.10 ", "qty": 3, "note": {"keep": [1, 2]}}]
        before = copy.deepcopy(lines)
        report(lines)
        self.assertEqual(lines, before)


class ReportRoundingTests(unittest.TestCase):
    def test_single_line_half_up(self):
        self.assertEqual(
            report([{"unit_price": "0.105", "qty": 1}])["total"], "0.11"
        )

    def test_rounds_only_final_sum(self):
        lines = [{"unit_price": "0.005", "qty": 1}] * 3
        # 0.005 * 3 = 0.015 -> half up once -> 0.02 (not 0.01 * 3)
        self.assertEqual(report(lines)["total"], "0.02")

    def test_per_line_rounding_would_differ(self):
        lines = [{"unit_price": "0.125", "qty": 1}, {"unit_price": "0.125", "qty": 1}]
        self.assertEqual(report(lines)["total"], "0.25")

    def test_large_sum_is_exact(self):
        lines = [{"unit_price": "12345678901234567890.005", "qty": 1}]
        self.assertEqual(report(lines)["total"], "12345678901234567890.01")


class ReportInvalidInputTests(unittest.TestCase):
    def test_non_list_input(self):
        for bad in (None, "x", 5, {"unit_price": "1", "qty": 1}, ("a",)):
            with self.subTest(bad=bad):
                self.assertEqual(report(bad), ERROR)

    def test_invalid_rows(self):
        for bad in (None, 3, "abc", ["1.00", 1]):
            with self.subTest(bad=bad):
                self.assertEqual(report([bad]), ERROR)

    def test_missing_required_fields(self):
        for line in ({"qty": 1}, {"unit_price": "1.00"}, {}, {"unit_price": "1", "q": 1}):
            with self.subTest(line=line):
                self.assertEqual(report([line]), ERROR)

    def test_invalid_prices(self):
        for raw in ("", "  ", "abc", "NaN", "Infinity", "-Infinity", "-1.00", "-0.01",
                    1.0, 1, None, Decimal("1"), True, [1]):
            with self.subTest(raw=raw):
                self.assertEqual(report([{"unit_price": raw, "qty": 1}]), ERROR)

    def test_invalid_quantities(self):
        for qty in (True, False, 1.0, "1", None, -1, Decimal("1")):
            with self.subTest(qty=qty):
                self.assertEqual(report([{"unit_price": "1.00", "qty": qty}]), ERROR)

    def test_one_bad_line_invalidates_all(self):
        lines = [{"unit_price": "1.00", "qty": 1}, {"unit_price": "bad", "qty": 1}]
        self.assertEqual(report(lines), ERROR)


class MoneyTests(unittest.TestCase):
    def test_formats_two_places(self):
        self.assertEqual(money(Decimal("0")), "0.00")
        self.assertEqual(money(Decimal("7.5")), "7.50")

    def test_rounds_half_up(self):
        self.assertEqual(money(Decimal("0.125")), "0.13")
        self.assertEqual(money(Decimal("0.135")), "0.14")
        self.assertEqual(money(Decimal("2.005")), "2.01")

    def test_legacy_module_removed(self):
        self.assertFalse(os.path.exists("rounding.py"))
        self.assertFalse(os.path.exists("legacy_round.py"))


class ParsingTests(unittest.TestCase):
    def test_parse_line_returns_decimal_and_int(self):
        price, qty = parse_line({"unit_price": " 1.5 ", "qty": 2})
        self.assertEqual(price, Decimal("1.5"))
        self.assertEqual(qty, 2)

    def test_parse_line_rejects_bad_rows(self):
        with self.assertRaises(InvalidLine):
            parse_line({"unit_price": "1", "qty": True})

    def test_parse_lines_rejects_non_list(self):
        with self.assertRaises(InvalidLine):
            parse_lines(iter([]))

    def test_total_does_not_mutate(self):
        lines = [{"unit_price": "1.00", "qty": 2}]
        before = copy.deepcopy(lines)
        self.assertEqual(total_for(lines), "2.00")
        self.assertEqual(lines, before)

    def test_total_accepts_parsed_pairs(self):
        self.assertEqual(total([(Decimal("1.00"), 2), (Decimal("0.25"), 2)]), "2.50")


if __name__ == "__main__":
    unittest.main()
