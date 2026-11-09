import copy
import unittest

from updates import apply_batch, revise_and_search


def rows():
    return [
        {'id': 'b', 'title': 'Beta', 'body': 'alpha beta'},
        {'id': 'a', 'title': 'Alpha', 'body': 'alpha gamma'},
    ]


class ApplyBatchTests(unittest.TestCase):
    def test_upsert_replacement_keeps_position_and_drops_stale_terms(self):
        edits = [{'op': 'upsert', 'document': {'id': 'b', 'title': 'New', 'body': 'delta'}}]
        out = apply_batch(rows(), edits)
        self.assertEqual([d['id'] for d in out], ['b', 'a'])
        self.assertEqual(out[0], {'id': 'b', 'title': 'New', 'body': 'delta'})
        result = revise_and_search(rows(), edits, 'beta')
        self.assertEqual(result['hits'], [])
        self.assertEqual(result['documents'], out)

    def test_delete_removes_document(self):
        out = apply_batch(rows(), [{'op': 'delete', 'id': 'b'}])
        self.assertEqual([d['id'] for d in out], ['a'])

    def test_new_id_appends(self):
        doc = {'id': 'c', 'title': 'Gamma', 'body': 'alpha'}
        out = apply_batch(rows(), [{'op': 'upsert', 'document': doc}])
        self.assertEqual([d['id'] for d in out], ['b', 'a', 'c'])
        self.assertEqual(out[-1], doc)

    def test_sequential_edits(self):
        edits = [
            {'op': 'upsert', 'document': {'id': 'b', 'title': 'B2', 'body': 'beta'}},
            {'op': 'upsert', 'document': {'id': 'b', 'title': 'B3', 'body': 'zeta'}},
            {'op': 'upsert', 'document': {'id': 'c', 'title': 'C', 'body': 'c'}},
            {'op': 'delete', 'id': 'a'},
        ]
        out = apply_batch(rows(), edits)
        self.assertEqual([d['id'] for d in out], ['b', 'c'])
        self.assertEqual(out[0]['title'], 'B3')

    def test_canonical_dicts_only(self):
        doc = {'id': 'c', 'title': 't', 'body': 'x', 'extra': 1}
        out = apply_batch([], [{'op': 'upsert', 'document': doc}])
        self.assertEqual(out, [{'id': 'c', 'title': 't', 'body': 'x'}])

    def test_failures_leave_inputs_unchanged(self):
        original = rows()
        snapshot = copy.deepcopy(original)
        bad_batches = [
            [{'op': 'delete', 'id': 'missing'}],
            [{'op': 'delete', 'id': ''}],
            [{'op': 'delete', 'id': 5}],
            [{'op': 'delete'}],
            [{'op': 'upsert'}],
            [{'op': 'upsert', 'document': {'id': 'x', 'title': 't'}}],
            [{'op': 'upsert', 'document': {'id': '', 'title': 't', 'body': 'b'}}],
            [{'op': 'rename', 'id': 'a'}],
            ['not a dict'],
            [{'op': 'delete', 'id': 'b'}, {'op': 'delete', 'id': 'zzz'}],
        ]
        for batch in bad_batches:
            with self.assertRaises(ValueError):
                apply_batch(original, batch)
            self.assertEqual(original, snapshot)

    def test_bad_rows_raise(self):
        dup = [{'id': 'a', 'title': 't', 'body': 'b'}, {'id': 'a', 'title': 't', 'body': 'b'}]
        with self.assertRaises(ValueError):
            apply_batch(dup, [])


class ReviseAndSearchTests(unittest.TestCase):
    def test_shape_without_phrase(self):
        edits = [{'op': 'delete', 'id': 'b'}]
        result = revise_and_search(rows(), edits, 'alpha')
        self.assertEqual(set(result), {'documents', 'hits'})
        self.assertEqual([h['id'] for h in result['hits']], ['a'])

    def test_shape_with_phrase(self):
        edits = [{'op': 'upsert', 'document': {'id': 'c', 'title': 'C', 'body': 'one two'}}]
        result = revise_and_search(rows(), edits, 'one', phrase='one two', slop=1)
        self.assertEqual(set(result), {'documents', 'hits', 'phrases'})
        self.assertEqual(result['phrases'], [{'id': 'c', 'starts': [0]}])

    def test_deviation_with_deviation_gap(self):
        edits = [{'op': 'upsert', 'document': {'id': 'c', 'title': 'C', 'body': 'one x two'}}]
        self.assertEqual(revise_and_search(rows(), edits, 'one', 'one two', 0)['phrases'], [])
        self.assertEqual(revise_and_search(rows(), edits, 'one', 'one two', 1)['phrases'],
                         [{'id': 'c', 'starts': [0]}])

    def test_preserves_inputs_on_edit_failure(self):
        original = rows()
        snapshot = copy.deepcopy(original)
        edits = [{'op': 'delete', 'id': 'b'}, {'op': 'delete', 'id': 'nope'}]
        with self.assertRaises(ValueError):
            revise_and_search(original, edits, 'alpha')
        self.assertEqual(original, snapshot)
        self.assertEqual(edits, [{'op': 'delete', 'id': 'b'}, {'op': 'delete', 'id': 'nope'}])

    def test_preserves_inputs_on_query_failure(self):
        original = rows()
        snapshot = copy.deepcopy(original)
        with self.assertRaises(ValueError):
            revise_and_search(original, [], 5)
        self.assertEqual(original, snapshot)
        with self.assertRaises(ValueError):
            revise_and_search(original, [], 'alpha', 'alpha', -1)
        self.assertEqual(original, snapshot)


if __name__ == '__main__':
    unittest.main()
