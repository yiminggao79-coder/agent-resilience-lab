import unittest
from stream import Decoder
from wire import encode_record, decode_line


def _row(tag, value, unit):
    return {'tag': tag, 'value': value, 'unit': unit}


class FreshState(unittest.TestCase):
    def test_fresh_decoder(self):
        self.assertEqual(
            Decoder().snapshot(),
            {'accepted_chars': 0, 'pending': '', 'records_seen': 0, 'closed': False},
        )


class ChunkingBehaviour(unittest.TestCase):
    def test_escape_split_across_chunks(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed('a|one\\'), [])
        self.assertEqual(decoder.snapshot()['pending'], 'a|one\\')
        self.assertEqual(decoder.feed('nthree|u\n'), [_row('a', 'one\nthree', 'u')])

    def test_pipe_escape_split_across_chunks(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed('a|1\\'), [])
        self.assertEqual(decoder.feed('|2|u\n'), [_row('a', '1|2', 'u')])

    def test_backslash_escape_split_across_chunks(self):
        decoder = Decoder()
        # `a|\\` + `|u\n` forms the physical line `a|\\|u\n`: value `\\|`
        # (escaped backslash then escaped pipe), unit `u`.
        self.assertEqual(decoder.feed('a|\\'), [])
        self.assertEqual(decoder.feed('|u\n'), [_row('a', '\\|', 'u')])

    def test_escape_only_carried_when_no_newline(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed('a|'), [])
        self.assertEqual(decoder.snapshot()['pending'], 'a|')

    def test_multiple_lines_one_chunk(self):
        decoder = Decoder()
        chunk = encode_record(_row('a', '1', 'u')) + encode_record(_row('b', '', ''))
        self.assertEqual(decoder.feed(chunk), [_row('a', '1', 'u'), _row('b', '', '')])
        self.assertEqual(decoder.snapshot()['pending'], '')

    def test_trailing_tail_retained(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed('a|1|u\nb|2'), [_row('a', '1', 'u')])
        self.assertEqual(decoder.snapshot()['pending'], 'b|2')

    def test_no_spurious_empty_record(self):
        decoder = Decoder()
        self.assertEqual(decoder.feed(encode_record(_row('a', '1', 'u'))), [_row('a', '1', 'u')])
        self.assertEqual(decoder.feed(''), [])

    def test_escaped_newline_does_not_terminate(self):
        decoder = Decoder()
        line = encode_record(_row('a', 'x\ny', 'u'))
        self.assertEqual(line, 'a|x\\ny|u\n')
        self.assertEqual(decoder.feed(line[:4]), [])
        self.assertEqual(decoder.feed(line[4:]), [_row('a', 'x\ny', 'u')])

    def test_unicode_intact(self):
        decoder = Decoder()
        line = encode_record(_row('温', '2', '℃'))
        self.assertEqual(decoder.feed(line[:2]), [])
        self.assertEqual(decoder.feed(line[2:]), [_row('温', '2', '℃')])
        self.assertEqual(decoder.snapshot()['accepted_chars'], len(line))


class Accounting(unittest.TestCase):
    def test_accepted_chars_and_records_seen(self):
        decoder = Decoder()
        first = encode_record(_row('a', '1', 'u'))
        decoder.feed(first)
        decoder.feed('b|2')
        state = decoder.snapshot()
        self.assertEqual(state['accepted_chars'], len(first) + len('b|2'))
        self.assertEqual(state['records_seen'], 1)
        decoder.feed('|u\n')
        self.assertEqual(decoder.snapshot()['records_seen'], 2)

    def test_counts_characters_not_bytes(self):
        decoder = Decoder()
        line = encode_record(_row('温', '2', '℃'))
        decoder.feed(line)
        self.assertEqual(decoder.snapshot()['accepted_chars'], len(line))


class Rollback(unittest.TestCase):
    def test_mid_chunk_failure_leaves_state(self):
        decoder = Decoder()
        decoder.feed('a|1')
        before = decoder.snapshot()
        good = encode_record(_row('b', '2', 'u'))
        with self.assertRaises(ValueError):
            decoder.feed(good + 'bad|\\q|u\n')
        self.assertEqual(decoder.snapshot(), before)
        # Later feed starts from the unchanged old tail.
        self.assertEqual(decoder.feed('|u\n'), [_row('a', '1', 'u')])
        self.assertEqual(decoder.snapshot()['records_seen'], 1)

    def test_earlier_valid_lines_not_emitted_on_failure(self):
        decoder = Decoder()
        first = encode_record(_row('a', '1', 'u'))
        with self.assertRaises(ValueError):
            decoder.feed(first + 'a|1|u|extra\n')
        self.assertEqual(decoder.snapshot()['records_seen'], 0)
        self.assertEqual(decoder.snapshot()['pending'], '')
        self.assertEqual(decoder.feed(first), [_row('a', '1', 'u')])

    def test_bad_field_count_failure(self):
        decoder = Decoder()
        decoder.feed('a|1|u\nb|2')
        before = decoder.snapshot()
        with self.assertRaises(ValueError):
            decoder.feed('|3|u|extra\n')
        self.assertEqual(decoder.snapshot(), before)


class Rejection(unittest.TestCase):
    def test_non_string_chunk(self):
        decoder = Decoder()
        decoder.feed('a|')
        before = decoder.snapshot()
        for bad in (b'a|1|u\n', None, 12, ['a|1|u\n']):
            with self.assertRaises(ValueError):
                decoder.feed(bad)
            self.assertEqual(decoder.snapshot(), before)

    def test_feed_after_close_even_empty(self):
        decoder = Decoder()
        decoder.finish()
        before = decoder.snapshot()
        for chunk in ('', 'a|1|u\n'):
            with self.assertRaises(ValueError):
                decoder.feed(chunk)
            self.assertEqual(decoder.snapshot(), before)


class FinishBehaviour(unittest.TestCase):
    def test_finish_with_pending_raises_and_keeps_state(self):
        decoder = Decoder()
        decoder.feed('a|1')
        before = decoder.snapshot()
        with self.assertRaises(ValueError):
            decoder.finish()
        self.assertEqual(decoder.snapshot(), before)
        self.assertFalse(decoder.snapshot()['closed'])

    def test_recovery_after_failed_finish(self):
        decoder = Decoder()
        decoder.feed('a|1')
        with self.assertRaises(ValueError):
            decoder.finish()
        self.assertEqual(decoder.feed('|u\n'), [_row('a', '1', 'u')])
        self.assertEqual(decoder.finish(), [])
        self.assertTrue(decoder.snapshot()['closed'])

    def test_finish_clean_and_repeat(self):
        decoder = Decoder()
        decoder.feed(encode_record(_row('a', '1', 'u')))
        self.assertEqual(decoder.finish(), [])
        self.assertEqual(decoder.finish(), [])
        self.assertTrue(decoder.snapshot()['closed'])

    def test_finish_never_synthesizes_newline(self):
        decoder = Decoder()
        decoder.feed('a|1|u')
        with self.assertRaises(ValueError):
            decoder.finish()
        self.assertEqual(decoder.snapshot()['pending'], 'a|1|u')


class ObjectOwnership(unittest.TestCase):
    def test_snapshot_isolated(self):
        decoder = Decoder()
        decoder.feed('a|1')
        snap = decoder.snapshot()
        snap['pending'] = 'mutated'
        snap['accepted_chars'] = 99
        self.assertEqual(decoder.snapshot()['pending'], 'a|1')
        self.assertEqual(decoder.snapshot()['accepted_chars'], 3)
        fresh = decoder.snapshot()
        self.assertIsNot(fresh, decoder.snapshot())

    def test_returned_records_are_owned(self):
        decoder = Decoder()
        records = decoder.feed('a|1|u\n')
        records[0]['value'] = 'changed'
        records.append(_row('z', 'z', 'z'))
        self.assertEqual(decoder.snapshot()['records_seen'], 1)
        decoder2 = Decoder()
        self.assertEqual(decoder2.feed('a|1|u\n'), [_row('a', '1', 'u')])

    def test_decode_matches_wire_for_roundtrip(self):
        decoder = Decoder()
        row = _row('a|b', 'one\\two\nthree', '')
        self.assertEqual(decoder.feed(encode_record(row)), [decode_line(encode_record(row))])
        self.assertEqual(decoder.snapshot()['records_seen'], 1)


if __name__ == '__main__':
    unittest.main()
