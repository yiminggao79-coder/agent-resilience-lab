"""Real file operations, bounded pages, and crash reconciliation; no model API."""
import json
import hashlib
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.process_guard import current_generation, worker_guard
from agent_lab.store import Store
from agent_lab.tools import Tools, ToolError, UncertainEffect


CRASH_DRIVER = '''
import json, signal, sys
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from tests.support import ScriptedModel, tool, final
root, database, mode = sys.argv[1:4]
with Store(database) as store:
    def hook(event, state):
        if mode == 'crash' and event == 'after_tool_effect':
            print(json.dumps({'task_id': state['id']}), flush=True)
            while True:
                signal.pause()
    model = ScriptedModel([tool('delete_file', {'path': 'remove.txt'}), final()]
                          if mode == 'crash' else [final('resumed')])
    engine = Engine(store, CodingTools(root), model, hook)
    task_id = engine.create('Delete remove.txt', Limits(max_seconds=120)) if mode == 'crash' else sys.argv[4]
    state = engine.run(task_id)
    print(json.dumps({'phase': state['phase'], 'reason': state['reason'], 'metrics': state['metrics']}), flush=True)
'''


class FileOperationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.tools = CodingTools(self.root)

    def call(self, name, args):
        return self.tools.execute(self.tools.prepare(name, args), 3)

    def test_create_directory_requires_explicit_parents_and_is_idempotent(self):
        args = {'path': 'pkg/nested', 'parents': False}
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('create_directory', args)
        self.assertEqual(error.exception.code, 'parent_not_found')
        self.assertFalse((self.root / 'pkg').exists())
        args['parents'] = True
        intent = self.tools.prepare('create_directory', args)
        self.assertFalse((self.root / 'pkg').exists())
        self.assertTrue(self.tools.execute(intent, 3)['created'])
        self.assertTrue((self.root / 'pkg/nested').is_dir())
        before = (self.root / 'pkg/nested').stat().st_ino
        recovered = self.tools.recover(intent, 3)
        self.assertTrue(recovered['reconciled'])
        self.assertFalse(recovered['created'])
        self.assertEqual((self.root / 'pkg/nested').stat().st_ino, before)
        self.assertFalse(self.call('create_directory', args)['created'])

    def test_create_directory_recovers_partial_parents_without_removing_contents(self):
        args = {'path': 'a/b/c', 'parents': True}
        intent = self.tools.prepare('create_directory', args)
        (self.root / 'a').mkdir()
        (self.root / 'a/keep').write_text('keep')
        self.tools.recover(intent, 3)
        self.assertTrue((self.root / 'a/b/c').is_dir())
        self.assertEqual((self.root / 'a/keep').read_text(), 'keep')

    def test_directory_changed_to_file_is_not_overwritten_during_recovery(self):
        intent = self.tools.prepare('create_directory', {'path': 'target', 'parents': False})
        (self.root / 'target').write_text('external')
        with self.assertRaises(UncertainEffect):
            self.tools.recover(intent, 3)
        self.assertEqual((self.root / 'target').read_text(), 'external')

    def test_delete_prepares_without_effect_and_recovery_confirms_absence(self):
        path = self.root / 'remove'
        path.write_bytes(b'\x00binary\xff')
        intent = self.tools.prepare('delete_file', {'path': 'remove'})
        self.assertTrue(path.exists())
        self.assertEqual(len(intent['before']), 64)
        first = self.tools.execute(intent, 3)
        self.assertEqual(first, {'ok': True, 'deleted': True, 'reconciled': False})
        self.assertFalse(path.exists())
        self.assertEqual(self.tools.recover(intent, 0), {'ok': True, 'deleted': True, 'reconciled': True})

    def test_delete_unchanged_prepared_file_can_be_retried(self):
        path = self.root / 'remove'
        path.write_text('original')
        intent = self.tools.prepare('delete_file', {'path': 'remove'})
        self.assertTrue(self.tools.recover(intent, 3)['deleted'])
        self.assertFalse(path.exists())

    def test_delete_changed_or_recreated_file_is_preserved(self):
        for after_delete in (False, True):
            with self.subTest(after_delete=after_delete):
                path = self.root / 'remove'
                path.write_text('original')
                intent = self.tools.prepare('delete_file', {'path': 'remove'})
                if after_delete:
                    self.tools.execute(intent, 3)
                path.write_text('changed by another writer')
                with self.assertRaises(UncertainEffect):
                    self.tools.recover(intent, 3)
                self.assertEqual(path.read_text(), 'changed by another writer')

    def test_delete_replacement_kind_or_link_is_preserved_and_large_input_is_rejected(self):
        path = self.root / 'remove'
        path.write_text('original')
        intent = self.tools.prepare('delete_file', {'path': 'remove'})
        path.unlink()
        path.mkdir()
        with self.assertRaises(UncertainEffect):
            self.tools.recover(intent, 3)
        self.assertTrue(path.is_dir())
        path.rmdir()
        (self.root / 'keep').write_text('original')
        path.symlink_to(self.root / 'keep')
        with self.assertRaises(UncertainEffect):
            self.tools.recover(intent, 3)
        self.assertEqual((self.root / 'keep').read_text(), 'original')
        (self.root / 'large').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('delete_file', {'path': 'large'})
        self.assertEqual(error.exception.code, 'file_too_large')

    def test_delete_detects_content_or_link_count_changes_while_hashing(self):
        for change in ('content', 'hardlink'):
            with self.subTest(change=change):
                path = self.root / change
                path.write_text('original')
                intent = self.tools._prepare('delete_file', {'path': change})
                real_fstat, inspected = os.fstat, [0]

                def race(descriptor):
                    inspected[0] += 1
                    if inspected[0] == 2:
                        if change == 'content':
                            path.write_text('external')
                        else:
                            os.link(path, self.root / 'extra_link')
                    return real_fstat(descriptor)

                with patch('agent_lab.coding_tools.os.fstat', side_effect=race):
                    with self.assertRaises(UncertainEffect):
                        self.tools._execute(intent, 3)
                self.assertTrue(path.exists())

    def test_delete_and_directory_recovery_never_mutate_after_expired_timeout(self):
        path = self.root / 'remove'
        path.write_text('original')
        intents = [self.tools.prepare('delete_file', {'path': 'remove'}),
                   self.tools.prepare('create_directory', {'path': 'new', 'parents': False})]
        for intent in intents:
            with self.subTest(name=intent['name']), self.assertRaises((ToolError, UncertainEffect)):
                self.tools.recover(intent, 0)
        self.assertEqual(path.read_text(), 'original')
        self.assertFalse((self.root / 'new').exists())

    def test_new_mutations_obey_worker_execution_lease(self):
        (self.root / 'remove').write_text('original')
        intents = [self.tools.prepare('delete_file', {'path': 'remove'}),
                   self.tools.prepare('create_directory', {'path': 'new', 'parents': False})]
        with worker_guard(self.root, current_generation(self.root)):
            for intent in intents:
                with self.subTest(name=intent['name']), self.assertRaises(UncertainEffect):
                    self.tools.execute(intent, 3)
        self.assertEqual((self.root / 'remove').read_text(), 'original')
        self.assertFalse((self.root / 'new').exists())

    def test_read_lines_numbers_and_pages_utf8_without_changing_legacy_read(self):
        text = 'alpha\n中文\r\nthird\nfourth'
        (self.root / 'code.py').write_text(text)
        first = self.call('read_lines', {'path': 'code.py', 'start_line': 2, 'end_line': 3})
        self.assertEqual(first, {'ok': True, 'path': 'code.py', 'lines': [
            {'line': 2, 'text': '中文'}, {'line': 3, 'text': 'third'}], 'next_line': 4, 'eof': False,
            'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()})
        last = self.call('read_lines', {'path': 'code.py', 'start_line': 4, 'end_line': 10})
        self.assertEqual(last['lines'], [{'line': 4, 'text': 'fourth'}])
        self.assertTrue(last['eof'])
        self.assertIsNone(last['next_line'])
        empty = self.call('read_lines', {'path': 'code.py', 'start_line': 10, 'end_line': 12})
        self.assertEqual(empty['lines'], [])
        self.assertTrue(empty['eof'])
        self.assertEqual(self.call('read_file', {'path': 'code.py'})['content'], text)

    def test_read_lines_pages_files_larger_than_legacy_read_limit(self):
        (self.root / 'large').write_text('line\n' * 20000)
        with self.assertRaises(ToolError) as error:
            self.call('read_file', {'path': 'large'})
        self.assertEqual(error.exception.code, 'file_too_large')
        result = self.call('read_lines', {'path': 'large', 'start_line': 15000, 'end_line': 15001})
        self.assertEqual(result['lines'], [{'line': 15000, 'text': 'line'}, {'line': 15001, 'text': 'line'}])

    def test_read_lines_empty_files_and_invalid_utf8_are_explicit(self):
        (self.root / 'empty').write_bytes(b'')
        result = self.call('read_lines', {'path': 'empty', 'start_line': 1, 'end_line': 2})
        self.assertEqual(result['lines'], [])
        self.assertTrue(result['eof'])
        (self.root / 'binary').write_bytes(b'\xff')
        with self.assertRaises(ToolError) as error:
            self.call('read_lines', {'path': 'binary', 'start_line': 1, 'end_line': 1})
        self.assertEqual(error.exception.code, 'invalid_data')

    def test_read_lines_reports_oversized_line_page_and_file_without_truncating(self):
        cases = [('line', 'x' * 65537, 1, 1, 'line_too_large'),
                 ('page', ('x' * 400 + '\n') * 200, 1, 200, 'response_too_large'),
                 ('file', 'x\n' * (1024 * 1024 + 1), 1, 1, 'file_too_large'),
                 ('escaped', ('\x00' * 100 + '\n') * 200, 1, 200, 'response_too_large')]
        for name, text, start, end, code in cases:
            with self.subTest(name=name):
                (self.root / name).write_text(text)
                with self.assertRaises(ToolError) as error:
                    self.call('read_lines', {'path': name, 'start_line': start, 'end_line': end})
                self.assertEqual(error.exception.code, code)

    def test_strict_arguments_reject_boolean_line_numbers_and_extra_fields(self):
        (self.root / 'a').write_text('a')
        cases = [('create_directory', {'path': 'new'}),
                 ('create_directory', {'path': 'new', 'parents': 1}),
                 ('create_directory', {'path': 'new', 'parents': False, 'mode': 511}),
                 ('delete_file', {'path': 'a', 'recursive': True}),
                 ('read_lines', {'path': 'a', 'start_line': True, 'end_line': 2}),
                 ('read_lines', {'path': 'a', 'start_line': 1, 'end_line': 2.0}),
                 ('read_lines', {'path': 'a', 'start_line': 0, 'end_line': 1}),
                 ('read_lines', {'path': 'a', 'start_line': 3, 'end_line': 2}),
                 ('read_lines', {'path': 'a', 'start_line': 1, 'end_line': 201}),
                 ('read_lines', {'path': 'a', 'start_line': 1})]
        for name, args in cases:
            with self.subTest(name=name, args=args), self.assertRaises(ToolError) as error:
                self.tools.prepare(name, args)
            self.assertEqual(error.exception.code, 'invalid_arguments')

    def test_paths_reject_hidden_escape_symlink_fifo_hardlink_and_wrong_kind(self):
        outside = self.root.parent / 'outside'
        outside.mkdir()
        (outside / 'sentinel').write_text('outside')
        (self.root / 'file').write_text('inside')
        (self.root / 'directory').mkdir()
        (self.root / 'symlink').symlink_to(outside, target_is_directory=True)
        os.link(self.root / 'file', self.root / 'hardlink')
        os.mkfifo(self.root / 'fifo')
        invalid = ['../outside/sentinel', '.git/config', '.private', '/tmp/file', 'symlink/sentinel', 'fifo', 'hardlink']
        for path in invalid:
            for name, extra in [('delete_file', {}), ('create_directory', {'parents': True}),
                                ('read_lines', {'start_line': 1, 'end_line': 1})]:
                with self.subTest(path=path, name=name), self.assertRaises(ToolError):
                    self.tools.prepare(name, {'path': path, **extra})
        for name, args in [('delete_file', {'path': 'directory'}),
                           ('delete_file', {'path': 'missing'}),
                           ('create_directory', {'path': 'file', 'parents': False})]:
            with self.subTest(name=name), self.assertRaises(ToolError):
                self.tools.prepare(name, args)
        self.assertEqual((outside / 'sentinel').read_text(), 'outside')

    def test_changed_parent_symlink_cannot_redirect_delete_or_directory_creation(self):
        inside = self.root / 'pkg'
        outside = self.root.parent / 'outside'
        inside.mkdir()
        outside.mkdir()
        (inside / 'remove').write_text('same')
        (outside / 'remove').write_text('same')
        intents = [self.tools.prepare('delete_file', {'path': 'pkg/remove'}),
                   self.tools.prepare('create_directory', {'path': 'pkg/new', 'parents': False})]
        inside.rename(self.root / 'old')
        inside.symlink_to(outside, target_is_directory=True)
        for intent in intents:
            with self.subTest(name=intent['name']), self.assertRaises((ToolError, UncertainEffect)):
                self.tools.execute(intent, 3)
        self.assertEqual((outside / 'remove').read_text(), 'same')
        self.assertFalse((outside / 'new').exists())

    def test_new_schema_is_strict_and_retains_existing_command_and_read_file(self):
        schemas = {s['function']['name']: s['function']['parameters'] for s in self.tools.schemas()}
        self.assertTrue({'create_directory', 'delete_file', 'read_lines', 'run_command'} <= set(schemas))
        self.assertEqual(schemas['read_file'], next(s['function']['parameters'] for s in Tools.schemas() if s['function']['name'] == 'read_file'))
        self.assertEqual(schemas['read_lines']['properties']['start_line']['type'], 'integer')
        self.assertEqual(schemas['create_directory']['properties']['parents']['type'], 'boolean')
        for name in ('create_directory', 'delete_file', 'read_lines'):
            self.assertFalse(schemas[name]['additionalProperties'])

    def test_sigkill_after_delete_before_receipt_recovers_in_a_fresh_process(self):
        (self.root / 'remove.txt').write_text('delete me')
        database = Path(self.tmp.name) / 'state.sqlite'
        proc = subprocess.Popen([sys.executable, '-c', CRASH_DRIVER, str(self.root), str(database), 'crash'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(15), 'child did not reach delete crash window')
            line = proc.stdout.readline()
            self.assertTrue(line, proc.stderr.read() if proc.poll() is not None else 'no ready event')
            task_id = json.loads(line)['task_id']
            self.assertFalse((self.root / 'remove.txt').exists())
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=8)
            self.assertEqual(proc.returncode, -signal.SIGKILL)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=8)
            proc.stdout.close()
            proc.stderr.close()
        with Store(database) as store:
            self.assertEqual(store.load(task_id)['pending']['status'], 'running')
        resumed = subprocess.run([sys.executable, '-c', CRASH_DRIVER, str(self.root), str(database), 'resume', task_id],
                                 text=True, capture_output=True, timeout=15)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        state = json.loads(resumed.stdout)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['metrics']['recoveries'], 1)
        self.assertFalse((self.root / 'remove.txt').exists())
