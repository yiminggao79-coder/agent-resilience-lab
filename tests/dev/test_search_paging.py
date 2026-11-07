"""Direct bounded literal search contracts over synthetic files; no model/API."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.tools import ToolError


class SearchPagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.tools = CodingTools(self.root)

    def search(self, args, timeout=5):
        from agent_lab.searching import prepare_search, execute_search
        return execute_search(self.tools, prepare_search(args)['args'], timeout)

    def test_more_than_200_files_and_matches_reconstructs_without_duplicates(self):
        for index in range(241):
            (self.root / ('file_%03d' % index)).write_text('needle twice needle\nneedle\n')
        matches, args = [], {'query': 'needle', 'limit': 73}
        while True:
            result = self.search(args)
            self.assertEqual(result['total_matches'], 482)
            self.assertEqual(result['scanned_files'], 241)
            matches.extend((row['path'], row['line'], row['column']) for row in result['matches'])
            if result['eof']:
                self.assertIsNone(result['next_cursor'])
                break
            self.assertTrue(result['next_cursor'].startswith('s1.'))
            args['cursor'] = result['next_cursor']
        self.assertEqual(len(matches), 482)
        self.assertEqual(len(set(matches)), 482)
        self.assertEqual(matches[:3], [('file_000', 1, 1), ('file_000', 2, 1), ('file_001', 1, 1)])
        self.assertEqual(matches[-1], ('file_240', 2, 1))

    def test_prefix_unicode_columns_literal_case_and_long_preview(self):
        (self.root / 'pkg').mkdir()
        (self.root / 'pkg/é.py').write_text('你好 x.y x.y\nX.Y\n' + 'λ' * 900 + 'x.y' + 'z' * 900 + '\n')
        (self.root / 'other').write_text('x.y\n')
        result = self.search({'query': 'x.y', 'prefix': 'pkg'})
        self.assertEqual(result['total_matches'], 2)
        short, long = result['matches']
        self.assertEqual((short['line'], short['column'], short['text']), (1, 4, '你好 x.y x.y'))
        self.assertEqual(short['preview_start_column'], 1)
        self.assertFalse(short['preview_truncated'])
        self.assertEqual((long['line'], long['column'], long['line_chars']), (3, 901, 1803))
        self.assertLessEqual(len(long['text']), 512)
        self.assertTrue(long['preview_truncated'])
        self.assertIn('x.y', long['text'])
        self.assertEqual(long['text'][901 - long['preview_start_column']:904 - long['preview_start_column']], 'x.y')

    def test_binary_oversize_skips_and_no_result_is_scoped(self):
        (self.root / 'binary').write_bytes(b'needle\xff')
        (self.root / 'nul').write_bytes(b'needle\0')
        (self.root / 'large').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        (self.root / 'text').write_text('different\n')
        result = self.search({'query': 'needle'})
        self.assertEqual(result['matches'], [])
        self.assertTrue(result['eof'])
        self.assertEqual(result['scanned_files'], 1)
        self.assertEqual(result['scanned_bytes'], 24)
        self.assertEqual(result['skipped_counts'], {'binary': 2, 'file_too_large': 1})
        self.assertEqual({e['path'] for e in result['skipped_examples']}, {'binary', 'nul', 'large'})

    def test_cursor_binds_options_root_and_observed_content(self):
        (self.root / 'a').write_text('needle\nneedle\n')
        first = self.search({'query': 'needle', 'limit': 1})
        for args in [{'query': 'other', 'limit': 1}, {'query': 'needle', 'limit': 2}]:
            args['cursor'] = first['next_cursor']
            with self.subTest(args=args), self.assertRaises(ToolError) as error:
                self.search(args)
            self.assertEqual(error.exception.code, 'invalid_cursor')
        other_root = Path(self.tmp.name) / 'other'
        other_root.mkdir()
        (other_root / 'a').write_text('needle\nneedle\n')
        from agent_lab.searching import execute_search
        with self.assertRaises(ToolError) as error:
            execute_search(CodingTools(other_root), {'query': 'needle', 'limit': 1, 'cursor': first['next_cursor']}, 5)
        self.assertEqual(error.exception.code, 'invalid_cursor')
        (self.root / 'a').write_text('needle\nchanged\n')
        with self.assertRaises(ToolError) as error:
            self.search({'query': 'needle', 'limit': 1, 'cursor': first['next_cursor']})
        self.assertEqual(error.exception.code, 'cursor_stale')

    def test_response_cap_shrinks_page_and_advances_actual_count(self):
        (self.root / 'a').write_text(('😀' * 500 + 'needle\n') * 201)
        first = self.search({'query': 'needle', 'limit': 200})
        self.assertGreater(len(first['matches']), 0)
        self.assertLess(len(first['matches']), 200)
        self.assertLessEqual(len(json.dumps({'result': first}, ensure_ascii=False).encode()) + 1, 65536)
        second = self.search({'query': 'needle', 'limit': 200, 'cursor': first['next_cursor']})
        self.assertEqual(second['matches'][0]['line'], len(first['matches']) + 1)

    def test_argument_validation_and_own_canonical_cursor(self):
        from agent_lab.searching import prepare_search, schema
        description = schema()['function']
        self.assertEqual(description['name'], 'search_files_page')
        self.assertEqual(description['parameters']['required'], ['query'])
        self.assertFalse(description['parameters']['additionalProperties'])
        self.assertEqual(prepare_search({'query': 'x'}), {'name': 'search_files_page',
                         'args': {'query': 'x', 'prefix': '', 'limit': 100}, 'policy': 'read'})
        for args in [{}, {'query': ''}, {'query': 'x\ny'}, {'query': 'x\ry'}, {'query': 'x\0y'},
                     {'query': 'x' * 1025}, {'query': '\ud800'}, {'query': 'x', 'limit': True},
                     {'query': 'x', 'limit': 0}, {'query': 'x', 'limit': 201},
                     {'query': 'x', 'prefix': '.hidden'}, {'query': 'x', 'prefix': '../escape'},
                     {'query': 'x', 'extra': 'no'}]:
            with self.subTest(args=repr(args)[:90]), self.assertRaises(ToolError):
                prepare_search(args)
        (self.root / 'a').write_text('x\nx\n')
        cursor = self.search({'query': 'x', 'limit': 1})['next_cursor']
        decoded = json.loads(base64.urlsafe_b64decode(cursor[3:]))
        malformed = ['d1.' + cursor[3:], cursor + '=', 's1.%%%!', 's1.' + 'a' * 1024]
        for update in [{'o': True}, {'o': 0}, {'o': 8 * 1024 * 1024 + 1}, {'q': 'not a hash'}, {'extra': 1}]:
            data = {**decoded, **update}
            malformed.append('s1.' + base64.urlsafe_b64encode(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).decode())
        for value in malformed:
            with self.subTest(cursor=value[:50]), self.assertRaises(ToolError) as error:
                prepare_search({'query': 'x', 'cursor': value})
            self.assertEqual(error.exception.code, 'invalid_cursor')
        # A canonical forged offset outside the result set is a resume hint,
        # never authority to return an invented empty page.
        decoded['o'] = 100
        forged = 's1.' + base64.urlsafe_b64encode(json.dumps(decoded, sort_keys=True, separators=(',', ':')).encode()).decode()
        with self.assertRaises(ToolError) as error:
            self.search({'query': 'x', 'limit': 1, 'cursor': forged})
        self.assertEqual(error.exception.code, 'invalid_cursor')

    def test_hidden_links_hardlinks_specials_excluded_and_prefix_checked(self):
        (self.root / 'allowed').write_text('needle\n')
        (self.root / '.hidden').write_text('needle\n')
        (self.root / 'link').symlink_to(self.root / 'allowed')
        (self.root / 'shared').write_text('needle\n')
        os.link(self.root / 'shared', self.root / 'alias')
        os.mkfifo(self.root / 'fifo')
        result = self.search({'query': 'needle'})
        self.assertEqual([row['path'] for row in result['matches']], ['allowed'])
        self.assertEqual(result['scanned_files'], 1)
        self.assertEqual(result['skipped_counts'], {'binary': 0, 'file_too_large': 0})
        with self.assertRaises(ToolError) as error:
            self.search({'query': 'needle', 'prefix': 'link'})
        self.assertEqual(error.exception.code, 'permission_denied')
        with self.assertRaises(ToolError) as error:
            self.search({'query': 'needle', 'prefix': 'allowed'})
        self.assertEqual(error.exception.code, 'not_a_directory')

    def test_total_read_budget_timeout_and_io_errors_fail_without_partial_page(self):
        from agent_lab import searching
        for index in range(5):
            (self.root / ('large%d' % index)).write_bytes(b'x' * (2 * 1024 * 1024))
        with self.assertRaises(ToolError) as error:
            self.search({'query': 'absent'})
        self.assertEqual(error.exception.code, 'search_too_large')
        for timeout in (0, -1, float('nan'), float('inf'), True):
            with self.subTest(timeout=timeout), self.assertRaises(ToolError) as error:
                self.search({'query': 'x'}, timeout)
            self.assertEqual(error.exception.code, 'tool_timeout')
        with patch.object(self.tools, '_read_at', side_effect=PermissionError('synthetic')):
            with self.assertRaises(ToolError) as error:
                self.search({'query': 'x'})
        self.assertEqual(error.exception.code, 'file_io_error')
        with patch.object(searching.time, 'monotonic', side_effect=iter(range(100000))):
            with self.assertRaises(ToolError) as error:
                self.search({'query': 'x'}, timeout=1)
        self.assertEqual(error.exception.code, 'tool_timeout')

    def test_many_skips_are_counted_with_only_20_examples(self):
        for index in range(30):
            (self.root / ('binary_%02d' % index)).write_bytes(b'\xff')
        result = self.search({'query': 'x'})
        self.assertEqual(result['skipped_counts']['binary'], 30)
        self.assertEqual(len(result['skipped_examples']), 20)
        self.assertEqual(result['scanned_bytes'], 30)
        self.assertTrue(result['eof'])

    def test_read_and_tree_changes_rejected_and_content_hash_survives_frozen_metadata(self):
        from agent_lab import searching
        (self.root / 'a').write_text('needle\nneedle\n')
        with patch.object(self.tools, '_read_at', side_effect=ToolError('file_changed')):
            with self.assertRaises(ToolError) as error:
                self.search({'query': 'needle'})
        self.assertEqual(error.exception.code, 'file_changed')
        scan = searching._scan
        original = scan(self.root, '', float('inf'))
        changed = (original[0], 'f' * 64, original[2])
        with patch.object(searching, '_scan', side_effect=[original, changed]):
            with self.assertRaises(ToolError) as error:
                self.search({'query': 'needle'})
        self.assertEqual(error.exception.code, 'tree_changed')
        with patch.object(searching, '_scan', return_value=original):
            first = self.search({'query': 'needle', 'limit': 1})
            # Pin only the metadata-observer boundary to prove actual file
            # bytes, not solely stat identity, contribute to result identity.
            (self.root / 'a').write_text('needle\nCHANGED\n')
            with self.assertRaises(ToolError) as error:
                self.search({'query': 'needle', 'limit': 1, 'cursor': first['next_cursor']})
        self.assertEqual(error.exception.code, 'cursor_stale')

    def test_search_coordinates_roundtrip_through_existing_read_lines(self):
        (self.root / 'a').write_bytes('a\u2028needle\r\nlast needle'.encode())
        result = self.search({'query': 'needle'})
        self.assertEqual([(m['line'], m['column']) for m in result['matches']], [(2, 1), (3, 6)])
        for match in result['matches']:
            read = self.tools._read_lines({'path': match['path'], 'start_line': match['line'], 'end_line': match['line']})
            self.assertEqual(read['lines'][0]['text'], match['text'])
        for separator in ('\u2028', '\u2029', '\x85', '\v', '\f', '\x1c', '\x1d', '\x1e'):
            with self.subTest(separator=repr(separator)), self.assertRaises(ToolError) as error:
                self.search({'query': separator})
            self.assertEqual(error.exception.code, 'invalid_arguments')


if __name__ == '__main__':
    unittest.main()
