import json
import unittest
from pathlib import Path
from quote import quote
class Public(unittest.TestCase):
    def test_configured(self):
        self.assertNotEqual(json.loads(Path("settings.json").read_text())["receipt"], "pending")
        self.assertIsInstance(quote(0), int)
