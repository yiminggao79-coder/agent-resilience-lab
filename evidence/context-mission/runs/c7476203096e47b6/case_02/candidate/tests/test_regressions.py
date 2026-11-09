"""Regression tests for tokenization, indexing and all-term scoring."""
import copy
import unittest

from corpus import documents
from inverted import build_index
from lexing import normalize, tokens
from reporting import describe
from searching import search


class LexingTests(unittest.TestCase):
    def test_punctuation_separates_tokens(self):
        self.assertEqual(tokens("a_b-c/d.e,f;g:h!i?j"), list('abcdefghij'))

    def test_unicode_is_alphanumeric(self):
        self.assertEqual(tokens("Ünïcode 漢字 Ωmega"), ['ünïcode', '漢字', 'ωmega'])

    def test_casefold_not_lower(self):
        # Casefold maps ß -> ss while lower() leaves it alone.
        self.assertEqual(tokens('Straße STRASSE'), ['strasse', 'strasse'])
        # Casefold leaves a combining dot after "i"; it must not split the token.
        self.assertEqual(tokens('İstanbul'), ['i̇stanbul'])
        self.assertEqual(tokens('İSTANBUL İstanbul'), ['i̇stanbul', 'i̇stanbul'])

    def test_curly_apostrophe_is_straightened(self):
        self.assertEqual(tokens('L\u2019\u00e9t\u00e9'), ["l'\u00e9t\u00e9"])
        self.assertEqual(tokens("isn't"), tokens('isn\u2019t'))

    def test_internal_apostrophes_only(self):
        self.assertEqual(tokens("'quoted'"), ['quoted'])
        self.assertEqual(tokens("rock 'n' roll"), ['rock', 'n', 'roll'])
        self.assertEqual(tokens("a''b"), ['a', 'b'])
        self.assertEqual(tokens("don't-stop"), ["don't", 'stop'])
        self.assertEqual(tokens("'"), [])
        self.assertEqual(tokens("''"), [])

    def test_whitespace_only(self):
        self.assertEqual(tokens(' \t\n\u00a0 '), [])

    def test_digits_and_underscores(self):
        self.assertEqual(tokens('_7_'), ['7'])
        self.assertEqual(tokens('v2 3rd'), ['v2', '3rd'])

    def test_non_string_rejected(self):
        for value in (None, 1, b'bytes', ['a']):
            with self.assertRaises(ValueError):
                tokens(value)

    def test_normalize_is_idempotent(self):
        text = "Stra\u00dfe L\u2019\u00e9t\u00e9"
        self.assertEqual(normalize(normalize(text)), normalize(text))
        self.assertEqual(tokens(normalize(text)), tokens(text))


class IndexTests(unittest.TestCase):
    def test_positions_follow_normalized_sequence(self):
        rows = [{'id': 'a', 'title': 'Red', 'body': "Red, red's a-b red"}]
        postings = build_index(rows)['postings']
        # "red's" is one token, so bare "red" sits at positions 0 and 4.
        self.assertEqual(postings['red'], {'a': [0, 4]})
        self.assertEqual(postings["red's"], {'a': [1]})
        self.assertEqual(postings['a'], {'a': [2]})
        self.assertEqual(postings['b'], {'a': [3]})

    def test_titles_not_indexed(self):
        rows = [{'id': 'a', 'title': 'visible title words', 'body': 'body'}]
        index = build_index(rows)
        self.assertEqual(sorted(index['postings']), ['body'])
        self.assertEqual(search(rows, 'title'), [])

    def test_metrics_use_body_tokens_only(self):
        rows = [{'id': 'a', 'title': 'x y z', 'body': 'one two'},
                {'id': 'b', 'title': '', 'body': 'two three three'}]
        self.assertEqual(describe(rows), {'documents': 2, 'terms': 3, 'tokens': 5})

    def test_empty_corpus_metrics(self):
        self.assertEqual(describe([]), {'documents': 0, 'terms': 0, 'tokens': 0})

    def test_index_is_owned_copy(self):
        rows = [{'id': 'a', 'title': 't', 'body': 'red'}]
        index = build_index(rows)
        index['documents']['a']['body'] = 'changed'
        index['postings']['red']['a'].append(99)
        self.assertEqual(rows, [{'id': 'a', 'title': 't', 'body': 'red'}])

    def test_validation_propagates(self):
        with self.assertRaises(ValueError):
            build_index([{'id': '', 'title': '', 'body': 'x'}])
        with self.assertRaises(ValueError):
            build_index([{'id': 'a', 'title': '', 'body': 'x'},
                         {'id': 'a', 'title': '', 'body': 'y'}])
        with self.assertRaises(ValueError):
            search([{'id': 'a', 'title': 1, 'body': 'x'}], 'x')


class SearchTests(unittest.TestCase):
    def test_repeated_terms_do_not_boost(self):
        rows = [{'id': 'x', 'title': '', 'body': 'red red blue'}]
        once = search(rows, 'red blue')
        many = search(rows, 'red red blue red')
        self.assertEqual(once, many)
        self.assertEqual(once, [{'id': 'x', 'score': 3, 'terms': {'red': 2, 'blue': 1}}])

    def test_distinct_requirement(self):
        rows = [{'id': 'x', 'title': '', 'body': 'red red'}]
        self.assertEqual(search(rows, 'red blue blue'), [])

    def test_query_normalized_before_matching(self):
        rows = [{'id': 'x', 'title': '', 'body': "Stra\u00dfe isn't l\u2019\u00e9t\u00e9"}]
        self.assertEqual([hit['id'] for hit in search(rows, "  STRASSE   ISN'T  L’ÉTÉ ")],
                         ['x'])
        self.assertEqual(search(rows, 'straße')[0]['terms'], {'strasse': 1})
        self.assertEqual(search(rows, "isn\u2019t")[0]['terms'], {"isn't": 1})

    def test_punctuation_only_query_returns_nothing(self):
        rows = [{'id': 'x', 'title': '', 'body': 'red'}]
        for query in ('', '   ', "'", "''", '---', "’", '_!?'):
            self.assertEqual(search(rows, query), [], query)

    def test_ordering_descending_score_then_id(self):
        rows = [
            {'id': 'c', 'title': '', 'body': 'red red red blue'},
            {'id': 'b', 'title': '', 'body': 'red blue blue'},
            {'id': 'a', 'title': '', 'body': 'red blue'},
            {'id': 'd', 'title': '', 'body': 'red'},
        ]
        self.assertEqual([(hit['id'], hit['score']) for hit in search(rows, 'red blue')],
                         [('c', 4), ('b', 3), ('a', 2)])

    def test_terms_map_only_distinct_positive_counts(self):
        rows = [{'id': 'a', 'title': '', 'body': 'x y y z'}]
        hit = search(rows, 'y x x y z').pop()
        self.assertEqual(hit['terms'], {'y': 2, 'x': 1, 'z': 1})
        self.assertEqual(hit['score'], 4)

    def test_missing_terms_absent_from_hits(self):
        rows = [{'id': 'a', 'title': '', 'body': 'x'}]
        self.assertEqual(search(rows, 'x nope'), [])

    def test_case_insensitive_matching(self):
        rows = [{'id': 'a', 'title': '', 'body': 'Hello WORLD'}]
        self.assertEqual(search(rows, 'hello world')[0]['terms'], {'hello': 1, 'world': 1})

    def test_inputs_preserved(self):
        rows = [{'id': 'a', 'title': 'T', 'body': 'red blue'}]
        before = copy.deepcopy(rows)
        result = search(rows, 'red red')
        self.assertEqual(rows, before)
        result[0]['terms']['red'] = 999
        result.append('junk')
        self.assertEqual(rows, before)
        self.assertEqual(search(rows, ' red ')[0]['terms'], {'red': 1})

    def test_empty_corpus(self):
        self.assertEqual(search([], 'red'), [])

    def test_result_dictionaries_are_independent(self):
        rows = [{'id': 'a', 'title': '', 'body': 'red'},
                {'id': 'b', 'title': '', 'body': 'red'}]
        first = search(rows, 'red')
        second = search(rows, 'red')
        self.assertEqual(first, second)
        first[0]['terms']['red'] = 0
        self.assertEqual(second[0]['terms'], {'red': 1})

    def test_query_non_string_rejected(self):
        with self.assertRaises(ValueError):
            search([], None)


if __name__ == '__main__':
    unittest.main()
