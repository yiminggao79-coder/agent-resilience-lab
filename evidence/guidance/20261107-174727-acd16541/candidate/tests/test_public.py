import unittest
from normalizer import normalize

class PublicTests(unittest.TestCase):
    def test_whitespace_and_order(self):
        values=[' B ', 'a', 'b', ' ']
        self.assertEqual(normalize(values), ['b','a'])
        self.assertEqual(values, [' B ', 'a', 'b', ' '])
