import copy
import unittest
from wire import encode_record, decode_line
from batch import load
from fields import record


class Escaping(unittest.TestCase):
    def test_round_trip_reserved(self):
        row = {'tag': 'a|b\\c', 'value': 'v|x\\y', 'unit': '|'}
        encoded = encode_record(row)
        self.assertEqual(encoded.count('\n'), 1)
        self.assertTrue(encoded.endswith('\n'))
        self.assertEqual(decode_line(encoded), row)

    def test_embedded_newline(self):
        row = {'tag': 'a', 'value': 'one\ntwo', 'unit': '\n'}
        encoded = encode_record(row)
        self.assertEqual(encoded, 'a|one\\ntwo|\\n\n')
        self.assertEqual(encoded.count('\n'), 1)
        self.assertEqual(decode_line(encoded), row)

    def test_literal_backslash_n(self):
        row = {'tag': 'a', 'value': '\\n', 'unit': ''}
        self.assertEqual(encode_record(row), 'a|\\\\n|\n')
        self.assertEqual(decode_line(encode_record(row)), row)

    def test_unicode(self):
        row = {'tag': '温|度', 'value': '℃\\', 'unit': 'µ\ns'}
        self.assertEqual(decode_line(encode_record(row)), row)

    def test_trailing_backslash(self):
        row = {'tag': 'a', 'value': 'end\\', 'unit': 'u'}
        self.assertEqual(decode_line(encode_record(row)), row)


class EmptyFields(unittest.TestCase):
    def test_empty_value_unit(self):
        row = {'tag': 'a', 'value': '', 'unit': ''}
        self.assertEqual(encode_record(row), 'a||\n')
        self.assertEqual(decode_line('a||\n'), row)

    def test_empty_value_or_unit(self):
        for line, expected in (('a|1|\n', {'tag': 'a', 'value': '1', 'unit': ''}),
                               ('a||u\n', {'tag': 'a', 'value': '', 'unit': 'u'})):
            self.assertEqual(decode_line(line), expected)

    def test_empty_field_encodes_to_nothing(self):
        row = {'tag': 'a', 'value': '', 'unit': 'u'}
        self.assertEqual(encode_record(row), 'a||u\n')


class Malformed(unittest.TestCase):
    def test_decode_rejects(self):
        bad = ['a|1|u', 'a|1|u\n\n', 'a|1|u\r\n', 'a|1|u\nb|2|u\n',
               'a|1', 'a|1|u|2', '', '\n', 'a\\|1|u\n',
               'a|1\\|u\n', 'a|bad\\q|u\n', 'a|1|u\\\n',
               'a|1|u\r\n', ' |1|u\n', 'a |1|u\n', 'a|\r|u\n']
        for line in bad:
            with self.assertRaises(ValueError, msg=repr(line)):
                decode_line(line)

    def test_decode_rejects_non_string(self):
        for value in (None, b'a|1|u\n', 3):
            with self.assertRaises(ValueError):
                decode_line(value)

    def test_encode_rejects_empty_tag(self):
        for tag in ('', '   ', ' a', 'a\ttrail '):
            with self.assertRaises(ValueError):
                encode_record({'tag': tag, 'value': '', 'unit': ''})

    def test_encode_rejects_carriage_return(self):
        with self.assertRaises(ValueError):
            encode_record({'tag': 'a', 'value': '\r', 'unit': ''})
        with self.assertRaises(ValueError):
            encode_record({'tag': 'a', 'value': '', 'unit': 'u\r'})

    def test_encode_preserves_caller_object(self):
        row = {'tag': 'a|b', 'value': '', 'unit': '\n'}
        before = copy.deepcopy(row)
        encoded = encode_record(row)
        self.assertEqual(row, before)
        self.assertEqual(len(decode_line(encoded)), 3)

    def test_record_returns_canonical_keys(self):
        self.assertEqual(sorted(record({'tag': 'a', 'value': '', 'unit': ''})),
                         ['tag', 'unit', 'value'])


class Batch(unittest.TestCase):
    def test_load_empty(self):
        self.assertEqual(load(''), [])

    def test_load_lines(self):
        rows = load('a|1|\n||\\|\n|x\n')
        self.assertEqual(rows, [{'tag': 'a', 'value': '1', 'unit': ''},
                                {'tag': '||', 'value': '\n', 'unit': 'x'}])

    def test_load_rejects_incomplete(self):
        with self.assertRaises(ValueError):
            load('a|1|u')
        with self.assertRaises(ValueError):
            load('a|1|u\nb|2|u')

    def test_load_rejects_bad_line(self):
        with self.assertRaises(ValueError):
            load('a|1|u\n\n')


if __name__ == '__main__':
    unittest.main()
