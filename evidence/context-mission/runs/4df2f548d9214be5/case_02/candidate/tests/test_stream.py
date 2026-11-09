import unittest

from stream import Decoder


class StreamBasics(unittest.TestCase):
    def test_fresh_snapshot(self):
        self.assertEqual(
            Decoder().snapshot(),
            {'accepted_chars': 0, 'pending': '', 'records_seen': 0, 'closed': False},
        )

    def test_multiple_lines_in_one_chunk(self):
        decoder = Decoder()
        rows = decoder.feed('a|1|u\nb|2|u\n')
        self.assertEqual(rows, [{'tag': 'a', 'value': '1', 'unit': 'u'},
                                {'tag': 'b', 'value': '2', 'unit': 'u'}])
        self.assertEqual(decoder.snapshot(),
                         {'accepted_chars': 8, 'pending': '', 'records_seen': 2, 'closed': False})

    def test_incomplete_tail_retained(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed('a|1|u\nb|2'), [{'tag': 'a', 'value': '1', 'unit': 'u'}])
        self.assertEqual(decoder.snapshot(),
                         {'accepted_chars': 8, 'pending': 'b|2', 'records_seen': 1, 'closed': False})
        self.assertEqual(decoder.feed('|u\n'), [{'tag': 'b', 'value': '2', 'unit': 'u'}])
        self.assertEqual(decoder.snapshot()['pending'], '')

    def test_split_escapes_across_chunks(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed('a|x'), [])
        self.assertEqual(decoder.feed('\\'), [])
        self.assertEqual(decoder.feed('|'), [])
        self.assertEqual(decoder.feed('\\'), [])
        self.assertEqual(decoder.feed('n'), [])
        self.assertEqual(decoder.feed('|u\n'), [{'tag': 'a', 'value': 'x|\\n', 'unit': 'u'}])

    def test_empty_fields_preserved(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed('a||\n'), [{'tag': 'a', 'value': '', 'unit': ''}])

    def test_snapshot_is_fresh(self):
        decoder = Decoder()
        snap = decoder.snapshot()
        snap['pending'] = 'x'
        snap['records_seen'] = 5
        self.assertEqual(decoder.snapshot()['pending'], '')
        self.assertEqual(decoder.snapshot()['records_seen'], 0)

    def test_records_are_independent(self):
        decoder = Decoder()
        rows = decoder.feed('a|1|u\n')
        rows[0]['tag'] = 'mutated'
        self.assertEqual(decoder.snapshot()['records_seen'], 1)
        self.assertEqual(decoder.feed('a|1|u\n'), [{'tag': 'a', 'value': '1', 'unit': 'u'}])


class StreamRollback(unittest.TestCase):
    def test_valid_then_invalid_rolls_back_all(self):
        decoder = Decoder()
        decoder.feed('old')
        before = decoder.snapshot()
        with self.assertRaises(ValueError):
            decoder.feed('a|1|u\nbad\n')
        self.assertEqual(decoder.snapshot(), before)
        # Old tail is unchanged and still usable.
        self.assertEqual(decoder.feed('|x\n'), [{'tag': 'old', 'value': 'x', 'unit': ''}])

    def test_invalid_incomplete_tail_not_decoded(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed('a|\\q'), [])


class StreamClose(unittest.TestCase):
    def test_non_string_chunk(self):
        decoder = Decoder()
        with self.assertRaises(ValueError):
            decoder.feed(None)
        with self.assertRaises(ValueError):
            decoder.feed(b'a|1|u\n')
        self.assertEqual(decoder.snapshot()['accepted_chars'], 0)

    def test_finish_with_pending(self):
        decoder = Decoder()
        decoder.feed('a|1')
        before = decoder.snapshot()
        with self.assertRaises(ValueError):
            decoder.finish()
        self.assertEqual(decoder.snapshot(), before)
        self.assertEqual(decoder.feed('|u\n'), [{'tag': 'a', 'value': '1', 'unit': 'u'}])
        self.assertEqual(decoder.finish(), [])
        self.assertTrue(decoder.snapshot()['closed'])

    def test_finish_repeated_and_closed_feed(self):
        decoder = Decoder()
        self.assertEqual(decoder.finish(), [])
        self.assertEqual(decoder.finish(), [])
        with self.assertRaises(ValueError):
            decoder.feed('')
        with self.assertRaises(ValueError):
            decoder.feed('a|1|u\n')
        self.assertEqual(decoder.snapshot(),
                         {'accepted_chars': 0, 'pending': '', 'records_seen': 0, 'closed': True})

    def test_finish_does_not_synthesize_newline(self):
        decoder = Decoder()
        decoder.feed('a|1')
        with self.assertRaises(ValueError):
            decoder.finish()
        self.assertEqual(decoder.snapshot()['pending'], 'a|1')


if __name__ == '__main__':
    unittest.main()
