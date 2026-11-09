import copy
import unittest
from stream import Decoder
from wire import encode_record

ROW = {'tag': 'a|b\\c', 'value': 'x\ny|z', 'unit': ''}


class Fresh(unittest.TestCase):
    def test_initial_state(self):
        d = Decoder()
        self.assertEqual(d.snapshot(), {'accepted_chars': 0, 'pending': '',
                                        'records_seen': 0, 'closed': False})
        snap = d.snapshot()
        snap['pending'] = 'mutated'
        snap['closed'] = True
        self.assertEqual(d.snapshot()['pending'], '')
        self.assertFalse(d.snapshot()['closed'])


class Feeding(unittest.TestCase):
    def test_split_escapes(self):
        text = encode_record(ROW)
        for cut in range(len(text) + 1):
            d = Decoder()
            out = d.feed(text[:cut]) + d.feed(text[cut:])
            self.assertEqual(out, [ROW], cut)
            self.assertEqual(d.snapshot()['accepted_chars'], len(text))
            self.assertEqual(d.snapshot()['records_seen'], 1)
            self.assertEqual(d.snapshot()['pending'], '')
            self.assertEqual(d.finish(), [])

    def test_multiple_lines_one_chunk(self):
        text = 'a|1|u\nb||\nc|2|\n'
        d = Decoder()
        self.assertEqual(d.feed(text), [
            {'tag': 'a', 'value': '1', 'unit': 'u'},
            {'tag': 'b', 'value': '', 'unit': ''},
            {'tag': 'c', 'value': '2', 'unit': ''},
        ])
        snap = d.snapshot()
        self.assertEqual(snap['accepted_chars'], len(text))
        self.assertEqual(snap['records_seen'], 3)
        self.assertEqual(snap['pending'], '')

    def test_incomplete_tail_retained(self):
        d = Decoder()
        self.assertEqual(d.feed('a|1|u\nb|2'), [{'tag': 'a', 'value': '1', 'unit': 'u'}])
        snap = d.snapshot()
        self.assertEqual(snap['pending'], 'b|2')
        self.assertEqual(snap['accepted_chars'], 10)
        self.assertEqual(d.feed('|u\n'), [{'tag': 'b', 'value': '2', 'unit': 'u'}])
        self.assertEqual(d.snapshot()['pending'], '')

    def test_empty_chunk(self):
        d = Decoder()
        self.assertEqual(d.feed(''), [])
        self.assertEqual(d.snapshot(), {'accepted_chars': 0, 'pending': '',
                                        'records_seen': 0, 'closed': False})

    def test_returned_records_do_not_share_state(self):
        d = Decoder()
        out = d.feed('a|1|u\n')
        out[0]['value'] = 'changed'
        self.assertEqual(d.snapshot()['records_seen'], 1)
        self.assertEqual(d.feed(''), [])

    def test_input_not_mutated(self):
        text = 'a|1|u\nb|'
        before = copy.deepcopy(text)
        d = Decoder()
        d.feed(text)
        self.assertEqual(text, before)


class Rollback(unittest.TestCase):
    def test_failure_discards_whole_feed(self):
        d = Decoder()
        d.feed('ok|1|u\npart')
        before = d.snapshot()
        good = {'tag': 'ok', 'value': '1', 'unit': 'u'}
        bad = 'also|2|u\nbad|x\n'
        with self.assertRaises(ValueError):
            d.feed(bad)
        self.assertEqual(d.snapshot(), before)

    def test_later_feed_resumes_from_old_tail(self):
        d = Decoder()
        d.feed('a|1|u\nb|')
        with self.assertRaises(ValueError):
            d.feed('2|u\n\n')
        self.assertEqual(d.snapshot()['pending'], 'b|')
        self.assertEqual(d.feed('2|u\n'), [{'tag': 'b', 'value': '2', 'unit': 'u'}])
        self.assertEqual(d.snapshot()['records_seen'], 2)

    def test_bad_escape_rolls_back(self):
        d = Decoder()
        d.feed('a|1|u\n')
        before = d.snapshot()
        with self.assertRaises(ValueError):
            d.feed('b|\\q|u\n')
        self.assertEqual(d.snapshot(), before)
        self.assertEqual(d.feed('c|3|u\n'), [{'tag': 'c', 'value': '3', 'unit': 'u'}])

    def test_non_string_chunk(self):
        d = Decoder()
        before = d.snapshot()
        for bad in (b'a|1|u\n', None, 3, ['a']):
            with self.assertRaises(ValueError):
                d.feed(bad)
            self.assertEqual(d.snapshot(), before)


class Finish(unittest.TestCase):
    def test_incomplete_finish_then_recovery(self):
        d = Decoder()
        d.feed('a|1|u\nb|2')
        before = d.snapshot()
        with self.assertRaises(ValueError):
            d.finish()
        self.assertEqual(d.snapshot(), before)
        self.assertEqual(d.feed('|u\n'), [{'tag': 'b', 'value': '2', 'unit': 'u'}])
        self.assertEqual(d.finish(), [])
        self.assertTrue(d.snapshot()['closed'])

    def test_repeated_finish(self):
        d = Decoder()
        self.assertEqual(d.finish(), [])
        self.assertEqual(d.finish(), [])
        self.assertTrue(d.snapshot()['closed'])

    def test_feed_after_close(self):
        d = Decoder()
        d.finish()
        before = d.snapshot()
        for chunk in ('', 'a|1|u\n'):
            with self.assertRaises(ValueError):
                d.feed(chunk)
            self.assertEqual(d.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
