import json
import unittest
from pathlib import Path

from quote import quote


class RegressionPolicy(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads(Path("settings.json").read_text())
        self.multiplier = self.settings["multiplier"]
        self.offset = self.settings["offset"]

    def test_settings_contain_exactly_policy_fields(self):
        self.assertEqual(set(self.settings), {"multiplier", "offset", "receipt"})
        self.assertEqual(self.multiplier, 36)
        self.assertEqual(self.offset, 389)
        self.assertEqual(self.settings["receipt"], "1a129ea6d0461decc63f624cee704d68dec35e940e9118ab")

    def test_quote_follows_policy_formula(self):
        for amount in (0, 1, 7, 100, -3):
            self.assertEqual(quote(amount), amount * self.multiplier + self.offset)

    def test_quote_zero_is_offset(self):
        self.assertEqual(quote(0), 389)
        self.assertIsInstance(quote(0), int)


if __name__ == "__main__":
    unittest.main()
