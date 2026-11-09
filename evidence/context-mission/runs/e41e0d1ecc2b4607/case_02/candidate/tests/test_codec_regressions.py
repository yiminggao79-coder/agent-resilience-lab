import copy
import unittest
from wire import encode_record, decode_line
from batch import load
from stats import counts

class Escaping(unittest.TestCase):
    def test_backslash_pipe_newline_round_trip(self):
        row = {'tag':'a\\\\b|c','value':'x|y\\z\nw','unit':'\\n|'}
        self.assertEqual(decode_line(encode_record(row)), row)
    def test_literal_backslash_n_is_not_newline(self):
        row = {'tag':'t','value':'\\n','unit':''}
        encoded = encode_record(row)
        self.assertEqual(encoded, 't|\\\\n|\n')
        self.assertEqual(decode_line(encoded), {'tag':'t','value':'\\n','unit':''})
    def test_doubled_backslash_then_n(self):
        row = {'tag':'t','value':'\\\n','unit':'u'}
        self.assertNotIn('\n', encode_record(row)[:-1])
        self.assertEqual(decode_line(encode_record(row)), row)
    def test_unicode_untouched(self):
        row = {'tag':'温|x','value':'é\\','unit':'℃\n'}
        self.assertEqual(decode_line(encode_record(row)), row)

class EmptyFields(unittest.TestCase):
    def test_all_empty(self):
        row = {'tag':'a','value':'','unit':''}
        self.assertEqual(encode_record(row), 'a||\n')
        self.assertEqual(decode_line('a||\n'), row)
    def test_empty_value_only_and_unit_only(self):
        self.assertEqual(decode_line('a||u\n'), {'tag':'a','value':'','unit':'u'})
        self.assertEqual(decode_line('a|1|\n'), {'tag':'a','value':'1','unit':''})
        with self.assertRaises(ValueError):
            decode_line('a|||\n')
        self.assertEqual(decode_line('a|\\||\n'), {'tag':'a','value':'|','unit':''})
    def test_empty_tag_rejected(self):
        for line in ('||\n', '||u\n', ' |1|u\n'):
            with self.assertRaises(ValueError):
                decode_line(line)
    def test_encode_empty_tag_rejected(self):
        with self.assertRaises(ValueError):
            encode_record({'tag':'','value':'1','unit':'u'})

class Malformed(unittest.TestCase):
    def test_missing_terminator(self):
        for line in ('a|1|u', '', 'a||', 'a|1|u\n\n'):
            with self.assertRaises(ValueError):
                decode_line(line)
    def test_raw_newline(self):
        with self.assertRaises(ValueError):
            decode_line('a|1\n|u\n')
        with self.assertRaises(ValueError):
            decode_line('a|1|u\nb|2|v\n')
    def test_unknown_and_dangling_escapes(self):
        for line in ('a|\\q|u\n', 'a|1|u\\\n', 'a\\\\\\|x\n', 'a|1\\x|u\n'):
            with self.assertRaises(ValueError):
                decode_line(line)
    def test_wrong_field_count(self):
        for line in ('a|1\n', 'a\n', 'a|1|u|x\n', '|1|u\n'):
            with self.assertRaises(ValueError):
                decode_line(line)
    def test_carriage_return(self):
        with self.assertRaises(ValueError):
            decode_line('a|1\r|u\n')
        with self.assertRaises(ValueError):
            encode_record({'tag':'a','value':'1','unit':'u\r'})
    def test_non_string(self):
        for value in (None, b'a|1|u\n', 5):
            with self.assertRaises(ValueError):
                decode_line(value)

class BatchAndCounts(unittest.TestCase):
    def test_batch_escaped_lines(self):
        rows = [{'tag':'a|b','value':'','unit':'u'}, {'tag':'c\\','value':'1\n2','unit':''}]
        text = ''.join(encode_record(row) for row in rows)
        self.assertEqual(load(text), rows)
        self.assertEqual(counts(load(text)), {'a|b':1, 'c\\':1})
    def test_batch_rejects_incomplete(self):
        with self.assertRaises(ValueError):
            load('a|1|u\nb|2|v')
    def test_counts_merges_escaped_tags(self):
        text = 'a\\|b|1|u\n' 'a\\|b|2|v\n'
        self.assertEqual(counts(load(text)), {'a|b':2})
    def test_caller_objects_preserved(self):
        row = {'tag':'a|b','value':'1\n','unit':'\\'}
        payload = {'batch':'a|1|u\n', 'record':row, 'line':'a|1|u\n'}
        before = copy.deepcopy(payload)
        encode_record(row)
        decode_line(payload['line'])
        counts(load(payload['batch']))
        self.assertEqual(payload, before)

if __name__ == '__main__':
    unittest.main()
