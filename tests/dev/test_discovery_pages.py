"""Paged discovery must not hide the remainder of an ordinary project tree."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.tools import MAX_BYTES, ToolError


class DiscoveryPagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.tools = CodingTools(self.root)

    def page(self, **args):
        return self.tools._execute(self.tools._prepare('list_files_page', args), 3)

    def assert_code(self, code, action):
        with self.assertRaises(ToolError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_reconstructs_more_than_200_files_in_canonical_order(self):
        expected = ['pkg/f%03d.py' % i for i in range(241)] + ['z.txt']
        (self.root / 'pkg').mkdir()
        for name in reversed(expected):
            (self.root / name).write_text('x')
        found, cursor, seen = [], None, set()
        while True:
            page = self.page(limit=37, **({'cursor': cursor} if cursor else {}))
            self.assertEqual(page['total_files'], 242)
            self.assertTrue(0 < len(page['files']) <= 37)
            found.extend(page['files'])
            if page['eof']:
                self.assertIsNone(page['next_cursor'])
                break
            cursor = page['next_cursor']
            self.assertNotIn(cursor, seen)
            seen.add(cursor)
        self.assertEqual(found, expected)
        self.assert_code('listing_too_large', lambda: self.tools._visible_files())

    def test_prefix_is_a_directory_subtree_and_empty_and_exact_last_page_end(self):
        (self.root / 'pkg').mkdir()
        (self.root / 'pkg2').mkdir()
        (self.root / 'pkg' / 'b').write_text('')
        (self.root / 'pkg' / 'a').write_text('')
        (self.root / 'pkg2' / 'c').write_text('')
        first = self.page(prefix='pkg', limit=1)
        self.assertEqual(first['files'], ['pkg/a'])
        last = self.page(prefix='pkg', limit=1, cursor=first['next_cursor'])
        self.assertEqual(last, {'ok': True, 'files': ['pkg/b'], 'next_cursor': None, 'eof': True, 'total_files': 2})
        empty = self.page(prefix='pkg2', limit=2)
        self.assertTrue(empty['eof'])
        (self.root / 'pkg2' / 'c').unlink()
        self.assertEqual(self.page(prefix='pkg2')['files'], [])
        self.assert_code('not_a_directory', lambda: self.page(prefix='pkg/a'))

    def test_changes_require_restart_and_query_cannot_reuse_cursor(self):
        for name in ('a', 'b'):
            (self.root / name).write_text(name)
        first = self.page(limit=1)
        self.assert_code('invalid_cursor', lambda: self.page(limit=2, cursor=first['next_cursor']))
        (self.root / 'c').write_text('c')
        self.assert_code('cursor_stale', lambda: self.page(limit=1, cursor=first['next_cursor']))
        self.assertEqual(self.page()['files'], ['a', 'b', 'c'])
        again = self.page(limit=1)
        (self.root / 'b').write_text('modified')
        self.assert_code('cursor_stale', lambda: self.page(limit=1, cursor=again['next_cursor']))

    def test_hidden_links_hardlinks_and_special_files_are_never_traversed(self):
        (self.root / 'visible').write_text('ok')
        (self.root / '.private').mkdir()
        (self.root / '.private' / 'secret').write_text('private')
        (self.root / 'link').symlink_to(self.root / '.private', target_is_directory=True)
        (self.root / 'hard1').write_text('x')
        os.link(self.root / 'hard1', self.root / 'hard2')
        os.mkfifo(self.root / 'fifo')
        self.assertEqual(self.page()['files'], ['visible'])
        self.assert_code('permission_denied', lambda: self.page(prefix='link'))
        self.assert_code('permission_denied', lambda: self.page(prefix='.private'))

    def test_strict_arguments_and_malformed_cursors(self):
        for args in ({'limit': True}, {'limit': 0}, {'limit': 201}, {'limit': 1.0},
                     {'prefix': None}, {'prefix': '../escape'}, {'prefix': '/absolute'},
                     {'prefix': 'a/'}, {'prefix': 'a\\b'}, {'cursor': None}, {'unknown': 1}):
            with self.subTest(args=args), self.assertRaises(ToolError):
                self.page(**args)
        for cursor in ('', 'garbage', 'd2.abc', 'd1.%%%%', 'd1.' + base64.urlsafe_b64encode(b'{}').decode(), 'x' * 2049):
            with self.subTest(cursor=cursor[:20]):
                self.assert_code('invalid_cursor', lambda: self.page(cursor=cursor))

    def test_scan_budget_fails_instead_of_skipping_entries(self):
        for i in range(8):
            (self.root / ('f%d' % i)).write_text('')
        with patch('agent_lab.discovery.MAX_ENTRIES', 7):
            self.assert_code('listing_too_large', self.page)
        self.assertEqual(len(self.page()['files']), 8)

    def test_response_fits_serialized_envelope_even_for_escaped_names(self):
        directory = self.root / ('\x01' * 90)
        directory.mkdir()
        expected = []
        for i in range(200):
            filename = '\x02' * 90 + '%03d' % i
            (directory / filename).write_text('')
            expected.append(directory.name + '/' + filename)
        found, cursor = [], None
        while True:
            page = self.page(limit=200, **({'cursor': cursor} if cursor else {}))
            self.assertLessEqual(len(json.dumps({'result': page}, ensure_ascii=False, allow_nan=False).encode('utf-8')), MAX_BYTES)
            self.assertGreater(len(page['files']), 0)
            found.extend(page['files'])
            if page['eof']:
                break
            cursor = page['next_cursor']
        self.assertEqual(found, expected)

    def test_serialized_budget_includes_worker_print_newline(self):
        for i in range(4):
            (self.root / ('x' * 240 + str(i))).write_text('')
        whole = self.page(limit=4)
        budget = len(json.dumps({'result': whole}, ensure_ascii=False, allow_nan=False).encode('utf-8'))
        with patch('agent_lab.discovery.MAX_BYTES', budget):
            page = self.page(limit=4)
        self.assertLessEqual(len(json.dumps({'result': page}, ensure_ascii=False, allow_nan=False).encode('utf-8')) + 1, budget)
        self.assertLess(len(page['files']), 4)
        self.assertFalse(page['eof'])

    def test_removed_prefix_invalidates_continuation(self):
        (self.root / 'pkg').mkdir()
        for name in ('a', 'b'):
            (self.root / 'pkg' / name).write_text('')
        first = self.page(prefix='pkg', limit=1)
        for name in ('a', 'b'):
            (self.root / 'pkg' / name).unlink()
        (self.root / 'pkg').rmdir()
        self.assert_code('cursor_stale', lambda: self.page(prefix='pkg', limit=1, cursor=first['next_cursor']))

    def test_depth_overflow_and_invalid_visible_names_fail_explicitly(self):
        directory = self.root
        for _ in range(33):
            directory = directory / 'd'
            directory.mkdir()
        self.assert_code('listing_too_large', self.page)
        directory.rmdir()
        directory.parent.rmdir()
        (self.root / 'back\\slash').write_text('')
        self.assert_code('invalid_path', self.page)

    def test_cursor_cannot_be_used_for_another_workspace(self):
        for name in ('a', 'b'):
            (self.root / name).write_text('')
        first = self.page(limit=1)
        other = Path(self.tmp.name) / 'other'
        other.mkdir()
        for name in ('a', 'b'):
            (other / name).write_text('')
        tools = CodingTools(other)
        intent = tools._prepare('list_files_page', {'limit': 1, 'cursor': first['next_cursor']})
        self.assert_code('invalid_cursor', lambda: tools._execute(intent, 3))

    def test_real_worker_round_trip_and_read_only_schema(self):
        (self.root / 'a').write_text('')
        intent = self.tools.prepare('list_files_page', {'limit': 1}, timeout=3)
        self.assertEqual(intent['policy'], 'read')
        self.assertEqual(self.tools.execute(intent, 3)['files'], ['a'])
        names = [schema['function']['name'] for schema in CodingTools.schemas()]
        self.assertEqual(names.count('list_files_page'), 1)
