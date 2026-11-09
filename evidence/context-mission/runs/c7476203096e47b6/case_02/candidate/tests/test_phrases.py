"""Focused tests for positional phrase retrieval (phrases.phrase_search)."""
import copy
import unittest

from phrases import phrase_search


def body(identity, text, title=''):
    return {'id': identity, 'title': title, 'body': text}


class PhraseOrderTests(unittest.TestCase):
    def test_adjacent_phrase(self):
        rows = [body('a', 'the quick brown fox')]
        self.assertEqual(phrase_search(rows, 'quick brown'), [{'id': 'a', 'starts': [1]}])

    def test_wrong_order_is_not_a_match(self):
        rows = [body('a', 'brown quick')]
        self.assertEqual(phrase_search(rows, 'quick brown'), [])

    def test_term_presence_alone_is_not_a_phrase(self):
        rows = [body('a', 'quick red brown')]
        self.assertEqual(phrase_search(rows, 'quick brown', slop=0), [])
        self.assertEqual(phrase_search(rows, 'quick brown', slop=1),
                         [{'id': 'a', 'starts': [0]}])

    def test_reversed_word_order_across_documents_never_bridges(self):
        rows = [body('a', 'quick'), body('b', 'brown')]
        self.assertEqual(phrase_search(rows, 'quick brown', slop=100), [])
        self.assertEqual(phrase_search(rows, 'brown quick', slop=100), [])

    def test_titles_are_not_searched(self):
        rows = [body('a', 'quick', title='quick brown'), body('b', 'brown')]
        self.assertEqual(phrase_search(rows, 'quick brown', slop=5), [])

    def test_query_normalization_applies(self):
        rows = [body('a', "Straße L\u2019\u00e9t\u00e9 isn't")]
        self.assertEqual(phrase_search(rows, "STRASSE L’ÉTÉ ISN'T"),
                         [{'id': 'a', 'starts': [0]}])

    def test_single_word_phrase_costs_zero(self):
        rows = [body('a', 'red red blue red')]
        self.assertEqual(phrase_search(rows, 'red'),
                         [{'id': 'a', 'starts': [0, 1, 3]}])
        # A single word needs no slop, and slop never restricts it.
        self.assertEqual(phrase_search(rows, 'red', slop=0),
                         phrase_search(rows, 'red', slop=7))
        self.assertEqual(phrase_search(rows, 'missing'), [])


class PhraseRepeatedTermTests(unittest.TestCase):
    def test_repeated_term_requires_distinct_increasing_positions(self):
        rows = [body('a', 'very very good')]
        self.assertEqual(phrase_search(rows, 'very very'), [{'id': 'a', 'starts': [0]}])
        # very at 0 and 2: start 0 needs (0,2), whose gap cost 2-0-1 = 1.
        self.assertEqual(phrase_search([body('b', 'very good very')], 'very very'), [])
        self.assertEqual(phrase_search([body('b', 'very good very')], 'very very', slop=1),
                         [{'id': 'b', 'starts': [0]}])

    def test_repeated_term_needs_two_occurrences(self):
        self.assertEqual(phrase_search([body('a', 'very good')], 'very very', slop=9), [])

    def test_triple_repeat_consumes_three_positions(self):
        # Starts 0 and 1 each cover three distinct positions; 2 cannot.
        rows = [body('a', 'a a a a')]
        self.assertEqual(phrase_search(rows, 'a a a'), [{'id': 'a', 'starts': [0, 1]}])
        self.assertEqual(phrase_search([body('b', 'a a')], 'a a a'), [])

    def test_adjacent_repeated_head_term(self):
        # go at 0,1,2; now at 3. The start position is consumed by the first
        # phrase token, so start 0 needs (0,1,3): cost 3-0-2 = 1, which slop 0
        # rejects. Only start 1 fits (1,2,3) at cost 0.
        rows = [body('a', 'go go go now')]
        self.assertEqual(phrase_search(rows, 'go go now', slop=0),
                         [{'id': 'a', 'starts': [1]}])
        self.assertEqual(phrase_search(rows, 'go go now', slop=1),
                         [{'id': 'a', 'starts': [0, 1]}])


class PhraseGapBudgetTests(unittest.TestCase):
    def test_exact_slop_threshold(self):
        rows = [body('a', 'quick red brown')]
        self.assertEqual(phrase_search(rows, 'quick brown', slop=0), [])
        self.assertEqual(phrase_search(rows, 'quick brown', slop=1),
                         [{'id': 'a', 'starts': [0]}])

    def test_cost_is_span_minus_expected_width(self):
        # Span 4 for 2 tokens -> cost 3; two tokens in between.
        rows = [body('a', 'w x y z end')]
        self.assertEqual(phrase_search(rows, 'w end', slop=2), [])
        self.assertEqual(phrase_search(rows, 'w end', slop=3),
                         [{'id': 'a', 'starts': [0]}])

    def test_three_token_phrase_internal_gaps(self):
        # "a c e" over 0..4 spans 4, minus (3-1) -> cost 2.
        rows = [body('a', 'a b c d e')]
        self.assertEqual(phrase_search(rows, 'a c e', slop=1), [])
        self.assertEqual(phrase_search(rows, 'a c e', slop=2),
                         [{'id': 'a', 'starts': [0]}])

    def test_large_slop_does_not_allow_reordering(self):
        rows = [body('a', 'e c a')]
        self.assertEqual(phrase_search(rows, 'a c e', slop=1000), [])

    def test_budget_is_per_match_not_shared(self):
        # p at 1, 3, 5; q only at 4. Start 1 -> p3,q4 cost 1; start 3 -> cost 0;
        # start 5 cannot reach q. Each start is judged on its own budget.
        rows = [body('a', 'x p y p q p y')]
        self.assertEqual(phrase_search(rows, 'p q', slop=1),
                         [{'id': 'a', 'starts': [3]}])
        # p at 1 reaches q at 4 with cost 4-1-1 = 2.
        self.assertEqual(phrase_search(rows, 'p q', slop=2),
                         [{'id': 'a', 'starts': [1, 3]}])

    def test_slop_validation(self):
        rows = [body('a', 'x y')]
        for bad in (True, False, -1, 1.0, '1', None):
            with self.assertRaises(ValueError):
                phrase_search(rows, 'x y', bad)


class PhraseMultipleContinuationsTests(unittest.TestCase):
    def test_start_counted_once_for_many_completions(self):
        # "a" at 0; "b" at 1, 2, 3 all give valid matches from start 0.
        rows = [body('a', 'a b b b')]
        self.assertEqual(phrase_search(rows, 'a b', slop=5),
                         [{'id': 'a', 'starts': [0]}])

    def test_each_qualifying_start_reported_once(self):
        rows = [body('a', 'x a b b a b')]
        # Starts: 1 (a b at 2) and 4 (a b at 5); start counted once each.
        self.assertEqual(phrase_search(rows, 'a b', slop=3),
                         [{'id': 'a', 'starts': [1, 4]}])

    def test_multiple_continuations_with_gaps(self):
        # b at 2 and 4: start 0 takes the earliest (0,2) with cost 1.
        rows = [body('a', 'a x b x b')]
        self.assertEqual(phrase_search(rows, 'a b', slop=5),
                         [{'id': 'a', 'starts': [0]}])
        self.assertEqual(phrase_search(rows, 'a b', slop=1),
                         [{'id': 'a', 'starts': [0]}])
        self.assertEqual(phrase_search(rows, 'a b', slop=0), [])

    def test_empty_document_starts(self):
        rows = [body('a', 'a a'), body('b', 'a')]
        self.assertEqual(phrase_search(rows, 'a a'),
                         [{'id': 'a', 'starts': [0]}])


class PhraseResultShapeTests(unittest.TestCase):
    def test_sorted_by_id_then_increasing_starts(self):
        rows = [body('z', 'go go'), body('a', 'go go go go'), body('m', 'nope')]
        result = phrase_search(rows, 'go')
        self.assertEqual([hit['id'] for hit in result], ['a', 'z'])
        self.assertEqual(result[0]['starts'], [0, 1, 2, 3])
        self.assertEqual(result[1]['starts'], [0, 1])

    def test_duplicate_starts_absent(self):
        rows = [body('a', 'a a a')]
        starts = phrase_search(rows, 'a a')[0]['starts']
        self.assertEqual(starts, sorted(set(starts)))

    def test_empty_phrase_returns_nothing(self):
        rows = [body('a', 'anything')]
        for phrase in ('', '   ', "''", '---', '\u2019'):
            self.assertEqual(phrase_search(rows, phrase), [], phrase)

    def test_empty_corpus(self):
        self.assertEqual(phrase_search([], 'a b'), [])

    def test_non_string_phrase_rejected(self):
        with self.assertRaises(ValueError):
            phrase_search([], None)


class PhraseValidationAndPurityTests(unittest.TestCase):
    def test_empty_phrase_still_validates_documents(self):
        with self.assertRaises(ValueError):
            phrase_search([{'id': 'a', 'title': '', 'body': 4}], '')
        with self.assertRaises(ValueError):
            phrase_search([body('a', 'x'), body('a', 'y')], '')

    def test_empty_phrase_still_validates_slop(self):
        with self.assertRaises(ValueError):
            phrase_search([], '', slop=-1)
        with self.assertRaises(ValueError):
            phrase_search([], '', slop=True)

    def test_duplicate_ids_and_malformed_rows(self):
        with self.assertRaises(ValueError):
            phrase_search([body('a', 'x'), body('a', 'x')], 'x')
        with self.assertRaises(ValueError):
            phrase_search([{'id': '', 'title': '', 'body': 'x'}], 'x')
        with self.assertRaises(ValueError):
            phrase_search([{'id': 'a', 'title': '', 'body': 'x'}], 5)

    def test_inputs_preserved(self):
        rows = [body('a', 'quick brown', title='Quick Brown')]
        before = copy.deepcopy(rows)
        result = phrase_search(rows, 'quick brown')
        self.assertEqual(rows, before)
        result[0]['starts'].append(99)
        result.append('junk')
        self.assertEqual(rows, before)
        self.assertEqual(phrase_search(rows, 'quick brown'),
                         [{'id': 'a', 'starts': [0]}])

    def test_results_independent_between_calls(self):
        rows = [body('a', 'x y')]
        first = phrase_search(rows, 'x')
        second = phrase_search(rows, 'x')
        first[0]['starts'].append(5)
        self.assertEqual(second[0]['starts'], [0])


if __name__ == '__main__':
    unittest.main()
