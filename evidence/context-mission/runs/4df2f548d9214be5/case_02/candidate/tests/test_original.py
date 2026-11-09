import copy
import unittest
from fields import record
from wire import encode_record, decode_line
from batch import load
from stats import counts

class OriginalContracts(unittest.TestCase):
    def test_simple_line(self):
        row = {'tag':'a','value':'1','unit':'u'}
        self.assertEqual(decode_line(encode_record(row)),row)
    def test_empty_fields(self):
        self.assertEqual(decode_line('a||\n'),{'tag':'a','value':'','unit':''})
    def test_escaped_pipe(self):
        row = {'tag':'a|b','value':'7','unit':'u'}
        self.assertEqual(decode_line(encode_record(row)),row)
    def test_escaped_backslash_and_newline(self):
        row = {'tag':'a','value':'one\\two\nthree','unit':'|'}
        self.assertEqual(decode_line(encode_record(row)),row)
    def test_unicode(self):
        row = {'tag':'温','value':'2','unit':'℃'}
        self.assertEqual(decode_line(encode_record(row)),row)
    def test_reject_unknown_escape(self):
        with self.assertRaises(ValueError):
            decode_line('a|\\q|u\n')
    def test_field_count(self):
        with self.assertRaises(ValueError):
            decode_line('a|1|u|extra\n')
    def test_batch_completeness(self):
        self.assertEqual(load(''),[])
        with self.assertRaises(ValueError):
            load('a|1|u')
    def test_owned_record(self):
        row = {'tag':'a','value':'1','unit':'u'}
        before = copy.deepcopy(row)
        record(row)['value'] = 'changed'
        self.assertEqual(row,before)
    def test_counts(self):
        rows = [{'tag':'a','value':'1','unit':'u'},{'tag':'a','value':'2','unit':'u'}]
        self.assertEqual(counts(rows),{'a':2})

if __name__ == '__main__':
    unittest.main()
