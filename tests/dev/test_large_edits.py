"""Version-bound edits above legacy write caps; real bounded workers, no API."""
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.tools import ToolError, UncertainEffect


def sha(data):
    return hashlib.sha256(data).hexdigest()


class LargeEditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.tools = CodingTools(self.root)
        self.data = b'start = OLD\n' + b'# padding padding padding\n' * 4000 + b'end = OLD2\n'
        self.path = self.root / 'module.py'
        self.path.write_bytes(self.data)
        self.path.chmod(0o755)

    def args(self, edits=None):
        return {'path': 'module.py', 'expected_sha256': sha(self.data),
                'edits': edits if edits is not None else [{'old': 'start = OLD', 'new': 'start = NEW'},
                                                        {'old': 'end = OLD2', 'new': 'end = NEW2'}]}

    def test_actual_large_edit_uses_read_lines_hash_and_keeps_intent_small(self):
        read = self.tools.execute(self.tools.prepare('read_lines', {'path': 'module.py', 'start_line': 1, 'end_line': 2}), 3)
        self.assertEqual(read.get('sha256'), sha(self.data))
        args = self.args()
        args['expected_sha256'] = read['sha256']
        intent = self.tools.prepare('edit_file', args)
        self.assertLess(len(json.dumps(intent).encode()), 4096)
        self.assertNotIn('content', intent['args'])
        result = self.tools.execute(intent, 3)
        self.assertTrue(result['ok'])
        self.assertEqual(self.path.read_bytes(), b'start = NEW\n' + b'# padding padding padding\n' * 4000 + b'end = NEW2\n')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o755)
        self.assertEqual(result['sha256'], sha(self.path.read_bytes()))

    def test_expected_version_and_ambiguous_missing_old_fail_before_effect(self):
        args = self.args()
        args['expected_sha256'] = '0' * 64
        with self.assertRaises(ToolError):
            self.tools.prepare('edit_file', args)
        for edits in [[{'old': 'padding', 'new': 'new'}], [{'old': 'not present', 'new': 'new'}]]:
            with self.subTest(edits=edits), self.assertRaises(ToolError):
                self.tools.prepare('edit_file', self.args(edits))
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_edits_are_ordered_and_may_delete_or_reuse_previous_result(self):
        intent = self.tools.prepare('edit_file', self.args([
            {'old': 'start = OLD', 'new': 'temporary'},
            {'old': 'temporary', 'new': 'FINAL'},
            {'old': 'end = OLD2', 'new': ''}]))
        self.tools.execute(intent, 3)
        self.assertTrue(self.path.read_bytes().startswith(b'FINAL\n# padding'))
        self.assertTrue(self.path.read_bytes().endswith(b'padding\n\n'))

    def test_changed_content_mode_and_corrupt_desired_hash_pause(self):
        intent = self.tools.prepare('edit_file', self.args())
        self.path.chmod(0o700)
        with self.assertRaises(UncertainEffect):
            self.tools.execute(intent, 3)
        self.assertEqual(self.path.read_bytes(), self.data)
        self.path.chmod(0o755)
        self.path.write_bytes(self.data + b'external')
        with self.assertRaises(UncertainEffect):
            self.tools.recover(intent, 3)
        self.path.write_bytes(self.data)
        corrupt = copy.deepcopy(intent)
        corrupt['desired'] = '0' * 64
        with self.assertRaises((ToolError, UncertainEffect)):
            self.tools.execute(corrupt, 3)
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_reconcile_after_effect_keeps_inode_mtime_and_checks_mode(self):
        intent = self.tools.prepare('edit_file', self.args())
        self.tools.execute(intent, 3)
        before = self.path.stat()
        result = self.tools.recover(intent, 0)
        after = self.path.stat()
        self.assertTrue(result['reconciled'])
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual(result['mode'], 0o755)
        self.path.chmod(0o700)
        with self.assertRaises(UncertainEffect):
            self.tools.recover(intent, 3)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o700)

    def test_argument_bounds_utf8_and_old_caps_stay_explicit(self):
        for change in [{'expected_sha256': 'A' * 64}, {'expected_sha256': '0' * 63},
                       {'edits': []}, {'edits': [{'old': '', 'new': 'x'}]},
                       {'edits': [{'old': 'x', 'new': 1}]},
                       {'edits': [{'old': 'x', 'new': 'x'}] * 17},
                       {'edits': [{'old': 'x', 'new': 'y' * 65536}]},
                       {'file_limit': 9999999}, {'mode': 0o777}]:
            args = {**self.args(), **change}
            with self.subTest(change=repr(change)[:80]), self.assertRaises(ToolError):
                self.tools.prepare('edit_file', args)
        for name, args in [('write_file', {'path': 'new', 'content': 'x' * 65537}),
                           ('apply_patch', {'path': 'module.py', 'old': 'start = OLD', 'new': 'new'})]:
            with self.subTest(name=name), self.assertRaises(ToolError):
                self.tools.prepare(name, args)
        self.path.write_bytes(b'\xffinvalid')
        args = self.args([{'old': 'invalid', 'new': 'new'}])
        args['expected_sha256'] = sha(self.path.read_bytes())
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('edit_file', args)
        self.assertEqual(error.exception.code, 'invalid_data')

    def test_source_and_each_intermediate_result_have_2mib_cap(self):
        data = b'MARK\n' + b'x' * (2 * 1024 * 1024 - 5)
        self.path.write_bytes(data)
        args = {'path': 'module.py', 'expected_sha256': sha(data),
                'edits': [{'old': 'MARK', 'new': 'x' * 65532}]}
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('edit_file', args)
        self.assertEqual(error.exception.code, 'file_too_large')
        self.path.write_bytes(data + b'x')
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('edit_file', args)
        self.assertEqual(error.exception.code, 'file_too_large')

    def test_readonly_special_links_and_recovery_keep_shared_boundaries(self):
        intent = self.tools.prepare('edit_file', self.args())
        protected = CodingTools(self.root, read_only_paths=['module.py'])
        for operation in (lambda: protected.prepare('edit_file', self.args()),
                          lambda: protected.execute(intent, 3), lambda: protected.recover(intent, 3)):
            with self.assertRaises((ToolError, UncertainEffect)):
                operation()
        self.path.chmod(0o1755)
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('edit_file', self.args())
        self.assertEqual(error.exception.code, 'special_mode_unsupported')
        self.path.chmod(0o755)
        (self.root / 'link').symlink_to(self.path)
        args = self.args(); args['path'] = 'link'
        with self.assertRaises(ToolError):
            self.tools.prepare('edit_file', args)
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_every_intermediate_is_bounded_even_if_final_would_shrink(self):
        data = b'MARK\n' + b'x' * (2 * 1024 * 1024 - 5)
        self.path.write_bytes(data)
        args = {'path': 'module.py', 'expected_sha256': sha(data), 'edits': [
            {'old': 'MARK', 'new': 'larger marker'},
            {'old': 'larger marker', 'new': 'small'}]}
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('edit_file', args)
        self.assertEqual(error.exception.code, 'file_too_large')
        self.assertEqual(self.path.read_bytes(), data)

    def test_tool_never_creates_files_or_follows_hardlinks_hidden_paths(self):
        for value in ('missing', 'missing/child', '.hidden', '../outside'):
            args = self.args(); args['path'] = value
            with self.subTest(path=value), self.assertRaises(ToolError):
                self.tools.prepare('edit_file', args)
        self.assertFalse((self.root / 'missing').exists())
        os.link(self.path, self.root / 'hardlink')
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('edit_file', self.args())
        self.assertEqual(error.exception.code, 'permission_denied')
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_changed_edits_cannot_reuse_old_prepared_desired_hash(self):
        intent = self.tools.prepare('edit_file', self.args())
        intent['args']['edits'][0]['new'] = 'different edit'
        with self.assertRaises(UncertainEffect):
            self.tools.execute(intent, 3)
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_read_lines_checksum_covers_bytes_outside_returned_slice(self):
        first = self.tools.execute(self.tools.prepare('read_lines', {'path': 'module.py', 'start_line': 1, 'end_line': 1}), 3)
        self.path.write_bytes(self.data + b'changed far below slice\n')
        second = self.tools.execute(self.tools.prepare('read_lines', {'path': 'module.py', 'start_line': 1, 'end_line': 1}), 3)
        self.assertEqual(first['lines'], second['lines'])
        self.assertNotEqual(first['sha256'], second['sha256'])
        self.assertEqual(second['sha256'], sha(self.path.read_bytes()))

    def test_read_lines_envelope_cap_includes_worker_newline(self):
        metadata = {'ok': True, 'path': 'module.py', 'sha256': '0' * 64,
                    'lines': [{'line': 1, 'text': ''}], 'next_line': None, 'eof': True}
        overhead = len(json.dumps({'result': metadata}, ensure_ascii=False).encode())
        self.path.write_text('x' * (65536 - overhead))
        # JSON alone would occupy exactly64KiB, leaving no byte for the worker
        # newline. The actual complete envelope must be rejected, not truncated.
        with self.assertRaises(ToolError) as error:
            self.tools.execute(self.tools.prepare('read_lines', {'path': 'module.py', 'start_line': 1, 'end_line': 1}), 3)
        self.assertEqual(error.exception.code, 'response_too_large')

    def test_edit_tool_is_discoverable_with_strict_required_arguments(self):
        schemas = {item['function']['name']: item['function'] for item in self.tools.schemas()}
        self.assertIn('edit_file', schemas)
        parameters = schemas['edit_file']['parameters']
        self.assertEqual(set(parameters['required']), {'path', 'expected_sha256', 'edits'})
        self.assertFalse(parameters['additionalProperties'])
        self.assertEqual(set(parameters['properties']), {'path', 'expected_sha256', 'edits'})
        edits = parameters['properties']['edits']
        self.assertEqual((edits['minItems'], edits['maxItems']), (1, 16))
        self.assertEqual(set(edits['items']['required']), {'old', 'new'})
        self.assertFalse(edits['items']['additionalProperties'])


if __name__ == '__main__':
    unittest.main()
