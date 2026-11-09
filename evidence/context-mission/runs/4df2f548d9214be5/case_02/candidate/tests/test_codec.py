import copy
import unittest
from wire import encode_record, decode_line
from batch import load
from stats import counts


class RoundTrip(unittest.TestCase):
    def test_escaped_reserved_characters(self):
        row = {'tag': 'a|b', 'value': 'x\\y|z\nw', 'unit': '\\|'}
        self.assertEqual(decode_line(encode_record(row)), row)

    def test_empty_value_and_unit(self):
        row = {'tag': 'a', 'value': '', 'unit': ''}
        self.assertEqual(encode_record(row), 'a||\n')
        self.assertEqual(decode_line(encode_record(row)), row)

    def test_empty_value_only(self):
        row = {'tag': 'a', 'value': '', 'unit': 'u'}
        self.assertEqual(encode_record(row), 'a||u\n')
        self.assertEqual(decode_line('a||u\n'), row)

    def test_escaped_newline_is_not_a_terminator(self):
        row = {'tag': 'a', 'value': 'one\ntwo', 'unit': 'u'}
        line = encode_record(row)
        self.assertEqual(line.count('\n'), 1)
        self.assertEqual(decode_line(line), row)

    def test_unicode_round_trip(self):
        row = {'tag': '温|度', 'value': '\n℃\\', 'unit': 'μ'}
        self.assertEqual(decode_line(encode_record(row)), row)

    def test_encode_does_not_mutate_input(self):
        row = {'tag': 'a|b', 'value': '1', 'unit': ''}
        before = copy.deepcopy(row)
        encode_record(row)
        self.assertEqual(row, before)


class Malformed(unittest.TestCase):
    def test_missing_terminator(self):
        with self.assertRaises(ValueError):
            decode_line('a|1|u')

    def test_raw_embedded_newline(self):
        with self.assertRaises(ValueError):
            decode_line('a|1\n|u\n')

    def test_unknown_escape(self):
        with self.assertRaises(ValueError):
            decode_line('a|\\q|u\n')

    def test_dangling_escape(self):
        with self.assertRaises(ValueError):
            decode_line('a|1|u\\\n')

    def test_wrong_separator_count(self):
        for line in ('a|1\n', 'a|1|u|2\n', 'a\n', 'a|||u\n'):
            with self.assertRaises(ValueError):
                decode_line(line)

    def test_empty_tag_rejected(self):
        with self.assertRaises(ValueError):
            decode_line('||\n')
        with self.assertRaises(ValueError):
            decode_line(' |1|u\n')

    def test_carriage_return_rejected(self):
        with self.assertRaises(ValueError):
            decode_line('a|1\r|u\n')

    def test_non_text_rejected(self):
        with self.assertRaises(ValueError):
            decode_line(None)
        with self.assertRaises(ValueError):
            decode_line(('a', '1', 'u'))

    def test_escaping_separator_is_one_field(self):
        self.assertEqual(decode_line('a\\|b|c|d\n'), {'tag': 'a|b', 'value': 'c', 'unit': 'd'})


class BatchAndCounts(unittest.TestCase):
    def test_batch_round_trip_with_empty_and_escapes(self):
        text = encode_record({'tag': 'a|b', 'value': '', 'unit': ''}) + encode_record(
            {'tag': 'a', 'value': 'n\nl', 'unit': 'u'}) + encode_record(
            {'tag': '温', 'value': '1', 'unit': '℃'})
        rows = load(text)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], {'tag': 'a|b', 'value': '', 'unit': ''})
        self.assertEqual(rows[1], {'tag': 'a', 'value': 'n\nl', 'unit': 'u'})
        self.assertEqual(rows[2], {'tag': '温', 'value': '1', 'unit': '℃'})

    def test_batch_empty_and_incomplete(self):
        self.assertEqual(load(''), [])
        with self.assertRaises(ValueError):
            load('a|1|u')
        with self.assertRaises(ValueError):
            load('a|1|u\nb|2|u')

    def test_counts_distinguishes_escaped_tags(self):
        rows = load('a|1|u\na|2|u\nb|3|u\nb|4|u\n')
        self.assertEqual([r['tag'] for r in rows], ['a', 'a', 'b', 'b'])
        self.assertEqual(counts(rows), {'a': 2, 'b': 2})

    def test_counts_keeps_empty_fields(self):
        rows = load('a||\na||\nb||u\n')
        self.assertEqual(counts(rows), {'a': 2, 'b': 1})

    def test_counts_does_not_mutate_rows(self):
        rows = [{'tag': 'a', 'value': '', 'unit': ''}]
        before = copy.deepcopy(rows)
        counts(rows)
        self.assertEqual(rows, before)


if __name__ == '__main__':
    unittest.main()
