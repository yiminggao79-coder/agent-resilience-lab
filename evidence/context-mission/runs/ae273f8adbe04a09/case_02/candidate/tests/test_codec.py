import copy
import unittest
from fields import record
from wire import encode_record, decode_line
from batch import load
from stats import counts

class Escaping(unittest.TestCase):
    def test_reserved_characters(self):
        row = {'tag': 'a|b\\c', 'value': 'x|y\\z', 'unit': '|\\'}
        encoded = encode_record(row)
        self.assertEqual(encoded, 'a\\|b\\\\c|x\\|y\\\\z|\\|\\\\\n')
        self.assertEqual(decode_line(encoded), row)

    def test_newline_escape(self):
        row = {'tag': 'a', 'value': '\n', 'unit': '\n\n'}
        encoded = encode_record(row)
        self.assertEqual(encoded, 'a|\\n|\\n\\n\n')
        self.assertEqual(encoded.count('\n'), 1)
        self.assertEqual(decode_line(encoded), row)

    def test_backslash_n_literal(self):
        row = {'tag': 'a', 'value': '\\n', 'unit': 'u'}
        self.assertEqual(encode_record(row), 'a|\\\\n|u\n')
        self.assertEqual(decode_line(encode_record(row)), row)

    def test_trailing_backslash(self):
        row = {'tag': 'a', 'value': 'end\\', 'unit': 'u'}
        self.assertEqual(decode_line(encode_record(row)), row)

    def test_pipe_in_tag(self):
        row = {'tag': 'a|b', 'value': '', 'unit': ''}
        self.assertEqual(encode_record(row), 'a\\|b||\n')
        self.assertEqual(decode_line('a\\|b||\n'), row)

    def test_unicode_preserved(self):
        row = {'tag': '温|度', 'value': '℃\\', 'unit': 'µ\ns'}
        self.assertEqual(decode_line(encode_record(row)), row)

    def test_round_trip_matrix(self):
        pieces = ['', 'a', '|', '\\', '\n', ' ', '温', 'n', '\\n', '\\\\|']
        for tag in pieces:
            if not tag.strip():
                continue
            for value in pieces:
                for unit in pieces:
                    row = {'tag': tag, 'value': value, 'unit': unit}
                    self.assertEqual(decode_line(encode_record(row)), row)

class EmptyFields(unittest.TestCase):
    def test_empty_value_and_unit(self):
        self.assertEqual(decode_line('a||\n'), {'tag': 'a', 'value': '', 'unit': ''})

    def test_empty_value_only(self):
        self.assertEqual(decode_line('a||u\n'), {'tag': 'a', 'value': '', 'unit': 'u'})

    def test_empty_unit_only(self):
        self.assertEqual(decode_line('a|1|\n'), {'tag': 'a', 'value': '1', 'unit': ''})

    def test_encode_empty(self):
        self.assertEqual(encode_record({'tag': 'a', 'value': '', 'unit': ''}), 'a||\n')

class MalformedRecords(unittest.TestCase):
    def bad(self, line):
        with self.assertRaises(ValueError):
            decode_line(line)

    def test_missing_terminator(self):
        self.bad('a|1|u')
        self.bad('')

    def test_raw_embedded_newline(self):
        self.bad('a|1\n|u\n')
        self.bad('a|1|u\n\n')
        self.bad('\na|1|u\n')

    def test_unknown_escapes(self):
        for line in ['a|\\q|u\n', 'a|\\t|u\n', 'a|\\0|u\n', '\\a|1|u\n', 'a|1|\\N\n']:
            self.bad(line)

    def test_dangling_escape(self):
        self.bad('a|\\\n')
        self.bad('a|1|u\\\n')

    def test_wrong_separator_count(self):
        self.bad('a|1\n')
        self.bad('a\n')
        self.bad('a|1|u|extra\n')
        self.bad('a||1||u\n')

    def test_non_string(self):
        self.bad(None)
        self.bad(b'a|1|u\n')

    def test_invalid_fields(self):
        self.bad('\n')            # empty tag
        self.bad(' a|1|u\n')      # untrimmed tag
        self.bad('a |1|u\n')
        self.bad('a|\r|u\n')      # carriage return
        self.bad('a|1|u\r\n')

    def test_escaped_newline_is_not_record_end(self):
        self.assertEqual(decode_line('a|\\n|u\n'),
                         {'tag': 'a', 'value': '\n', 'unit': 'u'})

    def test_encode_rejects_invalid(self):
        for row in [{'tag': '', 'value': '1', 'unit': 'u'},
                    {'tag': ' a', 'value': '1', 'unit': 'u'},
                    {'tag': 'a', 'value': 'x\ry', 'unit': 'u'},
                    {'tag': 'a', 'value': '1', 'unit': 'x\ry'},
                    {'tag': 'a', 'value': 1, 'unit': 'u'},
                    {'tag': 'a', 'value': '1'},
                    None]:
            with self.assertRaises(ValueError):
                encode_record(row)

class Preservation(unittest.TestCase):
    def test_encode_preserves_caller(self):
        row = {'tag': 'a|b', 'value': 'v\n', 'unit': 'u'}
        before = copy.deepcopy(row)
        encode_record(row)
        self.assertEqual(row, before)

    def test_decode_returns_fresh_owned_dict(self):
        row = decode_line('a||\n')
        self.assertEqual(row, {'tag': 'a', 'value': '', 'unit': ''})
        row['value'] = 'x'
        self.assertEqual(decode_line('a||\n')['value'], '')

    def test_decode_does_not_alias_input(self):
        line = 'a|1|u\n'
        decode_line(line)
        self.assertEqual(line, 'a|1|u\n')

class BatchAndStats(unittest.TestCase):
    def test_empty_batch(self):
        self.assertEqual(load(''), [])

    def test_batch_lines(self):
        rows = load('a|1|\n\\|\\||\\n|x\n')
        self.assertEqual(rows, [{'tag': 'a', 'value': '1', 'unit': ''},
                                {'tag': '||', 'value': '\n', 'unit': 'x'}])

    def test_batch_incomplete_final_line(self):
        with self.assertRaises(ValueError):
            load('a|1|u\nb|2|u')

    def test_batch_invalid_line(self):
        with self.assertRaises(ValueError):
            load('a|1|u\n\n')

    def test_counts_identity(self):
        rows = load('a|1|u\na|2|u\nb|3|\n')
        self.assertEqual(counts(rows), {'a': 2, 'b': 1})

    def test_counts_preserves_input(self):
        rows = [{'tag': 'a', 'value': '1', 'unit': 'u'}]
        before = copy.deepcopy(rows)
        counts(rows)
        self.assertEqual(rows, before)

if __name__ == '__main__':
    unittest.main()
