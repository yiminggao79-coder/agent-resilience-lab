import copy
import unittest
from corpus import documents
from lexing import tokens
from inverted import build_index
from searching import search
from reporting import describe

class OriginalContracts(unittest.TestCase):
    def test_tokens_punctuation(self):
        self.assertEqual(tokens("Red, RED-blue_7!"), ['red', 'red', 'blue', '7'])
    def test_apostrophe_and_unicode(self):
        self.assertEqual(tokens("L’été isn't STRASSE Straße"), ["l'été", "isn't", 'strasse', 'strasse'])
    def test_positions(self):
        rows = [{'id': 'b', 'title': 'not indexed', 'body': 'red blue red'}]
        self.assertEqual(build_index(rows)['postings']['red']['b'], [0, 2])
        self.assertNotIn('indexed', build_index(rows)['postings'])
    def test_repeated_query_is_not_weighted(self):
        rows = [{'id': 'x', 'title': '', 'body': 'red red blue'}]
        self.assertEqual(search(rows, 'red red blue'), [{'id': 'x', 'score': 3, 'terms': {'red': 2, 'blue': 1}}])
    def test_all_terms_required(self):
        rows = [{'id': 'x', 'title': '', 'body': 'red'}]
        self.assertEqual(search(rows, 'red blue'), [])
    def test_stable_tie(self):
        rows = [{'id': name, 'title': '', 'body': 'green'} for name in ['z', 'a']]
        self.assertEqual([hit['id'] for hit in search(rows, 'green')], ['a', 'z'])
    def test_blank_query(self):
        self.assertEqual(search([], ''), [])
    def test_metrics(self):
        self.assertEqual(describe([{'id': 'a', 'title': '', 'body': 'hi hi there'}]),
                         {'documents': 1, 'terms': 2, 'tokens': 3})
    def test_owned_copy(self):
        rows = [{'id': 'a', 'title': '', 'body': 'hi'}]
        before = copy.deepcopy(rows)
        documents(rows)[0]['body'] = 'changed'
        self.assertEqual(rows, before)
    def test_validation(self):
        with self.assertRaises(ValueError):
            documents([{'id': 'a', 'title': '', 'body': 4}])
        with self.assertRaises(ValueError):
            documents([{'id': 'a', 'title': '', 'body': ''}] * 2)

if __name__ == '__main__':
    unittest.main()
