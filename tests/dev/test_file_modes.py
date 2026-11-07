"""Ordinary rwx bits survive edits; mode conflicts stay uncertain."""
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine
from agent_lab.store import Store
from agent_lab.tools import Tools, ToolError, UncertainEffect
from contextlib import contextmanager
from tests.support import ScriptedModel, tool, final


def mode(path):
    return stat.S_IMODE(path.stat().st_mode)


class FileModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.tools = CodingTools(self.root)

    def file(self, name='script', permissions=0o755, text='old\n'):
        path = self.root / name
        path.write_text(text)
        path.chmod(permissions)
        return path

    def test_actual_worker_patch_preserves_755_instead_of_resetting_to_600(self):
        path = self.file()
        intent = self.tools.prepare('apply_patch', {'path': 'script', 'old': 'old', 'new': 'new'})
        result = self.tools.execute(intent, 3)
        self.assertTrue(result['ok'])
        self.assertEqual(path.read_text(), 'new\n')
        self.assertEqual(mode(path), 0o755)
        self.assertEqual(result['mode'], 0o755)

    def test_write_preserves_normal_bits_and_add_remains_private(self):
        for permissions in (0o644, 0o755, 0o700):
            with self.subTest(permissions=oct(permissions)):
                path = self.file(permissions=permissions)
                intent = self.tools.prepare('write_file', {'path': 'script', 'content': 'replacement\n'})
                self.assertEqual(intent['before_mode'], permissions)
                self.assertEqual(intent['desired_mode'], permissions)
                self.tools.execute(intent, 3)
                self.assertEqual(mode(path), permissions)
        intent = self.tools.prepare('write_file', {'path': 'new', 'content': 'private'})
        self.assertIsNone(intent['before_mode'])
        self.assertEqual(intent['desired_mode'], 0o600)
        self.tools.execute(intent, 3)
        self.assertEqual(mode(self.root / 'new'), 0o600)

    def test_chmod_before_execution_or_after_effect_never_reconciles_by_hash_alone(self):
        path = self.file()
        intent = self.tools.prepare('write_file', {'path': 'script', 'content': 'new\n'})
        path.chmod(0o700)
        with self.assertRaises(UncertainEffect):
            self.tools.execute(intent, 3)
        self.assertEqual(path.read_text(), 'old\n')
        path.chmod(0o755)
        self.tools.execute(intent, 3)
        path.chmod(0o700)
        for operation in (self.tools.execute, self.tools.recover):
            with self.assertRaises(UncertainEffect):
                operation(intent, 3)
        self.assertEqual(mode(path), 0o700)
        self.assertEqual(path.read_text(), 'new\n')

    def test_unchanged_desired_content_does_not_bypass_mode_conflict(self):
        path = self.file()
        intent = self.tools.prepare('write_file', {'path': 'script', 'content': 'old\n'})
        path.chmod(0o700)
        with self.assertRaises(UncertainEffect):
            self.tools.recover(intent, 3)
        self.assertEqual(mode(path), 0o700)

    def test_recovery_of_matching_content_and_mode_preserves_inode_mtime(self):
        path = self.file()
        intent = self.tools.prepare('write_file', {'path': 'script', 'content': 'new\n'})
        self.tools.execute(intent, 3)
        before = path.stat()
        result = self.tools.recover(intent, 0)
        after = path.stat()
        self.assertTrue(result['reconciled'])
        self.assertEqual(result['mode'], 0o755)
        self.assertEqual((before.st_ino, before.st_mtime_ns), (after.st_ino, after.st_mtime_ns))

    def test_legacy_hash_only_write_preserves_current_mode_when_it_still_executes(self):
        path = self.file(permissions=0o644)
        intent = self.tools.prepare('write_file', {'path': 'script', 'content': 'new\n'})
        intent.pop('before_mode', None)
        intent.pop('desired_mode', None)
        path.chmod(0o755)
        self.assertTrue(self.tools.recover(intent, 3)['ok'])
        self.assertEqual(mode(path), 0o755)
        self.assertTrue(self.tools.recover(intent, 0)['reconciled'])

    def test_special_bits_refused_for_mutations_but_read_still_works(self):
        path = self.file(permissions=0o1755)
        self.assertEqual(mode(path), 0o1755)
        operations = [('write_file', {'path': 'script', 'content': 'new'}),
                      ('append_file', {'path': 'script', 'content': 'new'}),
                      ('apply_patch', {'path': 'script', 'old': 'old', 'new': 'new'}),
                      ('delete_file', {'path': 'script'})]
        for name, args in operations:
            with self.subTest(name=name), self.assertRaises(ToolError) as error:
                self.tools.prepare(name, args)
            self.assertEqual(error.exception.code, 'special_mode_unsupported')
        read = self.tools.execute(self.tools.prepare('read_file', {'path': 'script'}), 3)
        self.assertEqual(read['content'], 'old\n')

    def test_patch_text_update_move_and_add_modes(self):
        self.file('update', 0o700)
        self.file('move', 0o755)
        body = ('*** Begin Patch\n*** Update File: update\n@@\n-old\n+updated\n'
                '*** Update File: move\n*** Move to: moved\n@@\n-old\n+moved\n'
                '*** Add File: added\n+new\n*** End Patch')
        with Store(Path(self.tmp.name) / 'state.sqlite') as store:
            engine = Engine(store, self.tools, ScriptedModel([tool('apply_patch_text', {'patch': body}), final()]))
            state = engine.run(engine.create('Apply patch'))
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(mode(self.root / 'update'), 0o700)
        self.assertEqual(mode(self.root / 'moved'), 0o755)
        self.assertEqual(mode(self.root / 'added'), 0o600)
        self.assertFalse((self.root / 'move').exists())

    def test_patch_initial_mode_vector_and_move_destination_mode_guard(self):
        self.file('first', 0o755)
        later = self.file('later', 0o644)
        intent = self.tools.prepare('apply_patch_text', {'patch':
            '*** Begin Patch\n*** Update File: first\n@@\n-old\n+new\n*** Delete File: later\n*** End Patch'})
        later.chmod(0o700)
        with self.assertRaises(UncertainEffect):
            self.tools.execute(intent['steps'][0], 3)
        self.assertEqual((self.root / 'first').read_text(), 'old\n')
        intent = self.tools.prepare('apply_patch_text', {'patch':
            '*** Begin Patch\n*** Update File: first\n*** Move to: dest\n@@\n-old\n+new\n*** End Patch'})
        self.tools.execute(intent['steps'][0], 3)
        (self.root / 'dest').chmod(0o700)
        with self.assertRaises(UncertainEffect):
            self.tools.execute(intent['steps'][1], 3)
        self.assertTrue((self.root / 'first').exists())

    def test_delete_mode_change_is_not_ignored(self):
        path = self.file()
        intent = self.tools.prepare('delete_file', {'path': 'script'})
        path.chmod(0o700)
        with self.assertRaises(UncertainEffect):
            self.tools.execute(intent, 3)
        self.assertTrue(path.exists())

    def test_same_fd_snapshot_rejects_mode_change_during_content_read(self):
        path = self.file()
        original_fdopen = os.fdopen
        @contextmanager
        def mutating_fdopen(fd, *args, **kwargs):
            with original_fdopen(fd, *args, **kwargs) as stream:
                class ChangingReader:
                    def fileno(self):
                        return stream.fileno()
                    def read(self, limit):
                        data = stream.read(limit)
                        path.chmod(0o700)
                        return data
                yield ChangingReader()
        with patch('agent_lab.tools.os.fdopen', side_effect=mutating_fdopen):
            with self.assertRaises(ToolError) as error:
                self.tools._prepare('write_file', {'path': 'script', 'content': 'new'})
        self.assertEqual(error.exception.code, 'file_changed')
        self.assertEqual(path.read_text(), 'old\n')
        self.assertEqual(mode(path), 0o700)

    def test_temp_mode_is_set_before_fsync_and_atomic_replace(self):
        path = self.file()
        intent = self.tools._prepare('write_file', {'path': 'script', 'content': 'new'})
        actual_chmod, actual_sync, actual_replace = os.fchmod, os.fsync, os.replace
        events = []
        def chmod(fd, permissions):
            actual_chmod(fd, permissions)
            events.append(('chmod', stat.S_IMODE(os.fstat(fd).st_mode)))
        def sync(fd):
            events.append(('sync', stat.S_IMODE(os.fstat(fd).st_mode)))
            actual_sync(fd)
        def replace(source, destination):
            events.append(('replace', mode(Path(source))))
            self.assertEqual(Path(source).read_text(), 'new')
            actual_replace(source, destination)
        with patch('agent_lab.tools.os.fchmod', side_effect=chmod), \
             patch('agent_lab.tools.os.fsync', side_effect=sync), \
             patch('agent_lab.tools.os.replace', side_effect=replace):
            self.tools._execute(intent, 3)
        self.assertEqual(events[:3], [('chmod', 0o755), ('sync', 0o755), ('replace', 0o755)])
        self.assertEqual(mode(path), 0o755)
        self.assertEqual(events[3][0], 'sync')

    def test_compound_special_bits_refused_before_any_effect(self):
        self.file('first', 0o755)
        self.file('special', 0o1755)
        text = '*** Begin Patch\n*** Update File: first\n@@\n-old\n+new\n*** Delete File: special\n*** End Patch'
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('apply_patch_text', {'patch': text})
        self.assertEqual(error.exception.code, 'special_mode_unsupported')
        self.assertEqual((self.root / 'first').read_text(), 'old\n')
        self.assertEqual(mode(self.root / 'special'), 0o1755)


if __name__ == '__main__':
    unittest.main()
