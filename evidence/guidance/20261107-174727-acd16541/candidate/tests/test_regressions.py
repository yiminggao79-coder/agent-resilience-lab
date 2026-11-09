import unittest

from labelkit.normalize import canonical_keys
from normalizer import normalize


class CanonicalKeysRegressions(unittest.TestCase):
    def test_casefold_not_lower_for_unicode(self):
        # 'ß'.lower() == 'ß' but 'ß'.casefold() == 'ss', so these collapse.
        self.assertEqual(canonical_keys(['ß', 'SS']), ['ss'])

    def test_unicode_whitespace_is_trimmed(self):
        # U+00A0 NBSP, U+2003 EM SPACE, U+3000 IDEOGRAPHIC SPACE
        self.assertEqual(canonical_keys(['\u00a0Ab\u2003', '\u3000ab\u3000']), ['ab'])

    def test_empty_and_whitespace_only_are_discarded(self):
        self.assertEqual(canonical_keys(['', '   ', '\u00a0', 'x']), ['x'])

    def test_first_occurrence_order_preserved(self):
        self.assertEqual(canonical_keys(['b', 'A', 'B', 'a']), ['b', 'a'])

    def test_input_not_mutated_and_new_list(self):
        values = [' B ', 'a', 'b', ' ']
        out = canonical_keys(values)
        self.assertEqual(values, [' B ', 'a', 'b', ' '])
        self.assertIsNot(out, values)
        self.assertEqual(out, ['b', 'a'])

    def test_normalize_delegates(self):
        self.assertEqual(normalize(['ß', 'ss', ' ']), ['ss'])


if __name__ == '__main__':
    unittest.main()
