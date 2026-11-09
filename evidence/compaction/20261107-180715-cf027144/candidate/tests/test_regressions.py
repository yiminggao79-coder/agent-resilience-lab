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


class NormalizeExcludeRegressions(unittest.TestCase):
    def test_none_preserves_old_behavior(self):
        self.assertEqual(normalize([' B ', 'a', 'b', ' ']), ['b', 'a'])
        self.assertEqual(normalize([' B ', 'a', 'b', ' '], None), ['b', 'a'])

    def test_exclude_omits_matching_keys(self):
        self.assertEqual(normalize(['a', 'b', 'c'], exclude=['B']), ['a', 'c'])

    def test_exclude_uses_same_canonical_rules(self):
        # whitespace-trimmed, casefolded, empty-discarded exclusions
        self.assertEqual(
            normalize(['ß', 'a', 'b'], exclude=[' SS ', '\u00a0', '  ']), ['a', 'b']
        )
        self.assertEqual(
            normalize(['stra\u00dfe', 'strasse', 'x'], exclude=['STRASSE']),
            ['x'],
        )

    def test_exclude_ordering_preserved(self):
        self.assertEqual(normalize(['b', 'A', 'c', 'a'], exclude=['A']), ['b', 'c'])

    def test_empty_exclude_changes_nothing(self):
        self.assertEqual(normalize(['a', 'b'], exclude=[]), ['a', 'b'])
        self.assertEqual(normalize(['a', 'b'], exclude=['   ']), ['a', 'b'])

    def test_excluding_everything_returns_empty_list(self):
        self.assertEqual(normalize(['a', 'B'], exclude=['b', ' a ']), [])

    def test_neither_input_mutated(self):
        values = [' B ', 'a', 'b', ' ']
        exclude = [' B ', ' ']
        out = normalize(values, exclude)
        self.assertEqual(values, [' B ', 'a', 'b', ' '])
        self.assertEqual(exclude, [' B ', ' '])
        self.assertEqual(out, ['a'])
        self.assertIsNot(out, values)
        self.assertIsNot(out, exclude)


if __name__ == '__main__':
    unittest.main()
