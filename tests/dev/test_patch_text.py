"""Bounded patch grammar and durable per-file application; offline contracts."""
import json
import selectors
import signal
import subprocess
import sys
import time
from unittest.mock import patch
from pathlib import Path
import tempfile
import unittest

from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store, StorageUnavailable
from agent_lab.tools import ToolError
from tests.support import ScriptedModel, tool, final


def patch_text(body):
    return '*** Begin Patch\n' + body + '\n*** End Patch'


CRASH_DRIVER = r"""
import json, signal, sys
from agent_lab.coding_tools import CodingTools
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store
from tests.support import ScriptedModel, tool, final
root, database, mode, body = sys.argv[1:5]
with Store(database) as store:
    def hook(event, state):
        if event == 'after_tool_effect' and mode == 'crash':
            print(json.dumps({'id': state['id'], 'pid': __import__('os').getpid()}), flush=True)
            while True: signal.pause()
    model = ScriptedModel([tool('apply_patch_text', {'patch': body}, 'patch_call'), final()]
                          if mode == 'crash' else [final('resumed')])
    engine = Engine(store, CodingTools(root), model, hook)
    task_id = engine.create('Apply two files', Limits(max_seconds=60)) if mode == 'crash' else sys.argv[5]
    state = engine.run(task_id)
    print(json.dumps({'state': state, 'pid': __import__('os').getpid()}), flush=True)
"""


class PatchTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'work'
        self.root.mkdir()
        self.tools = CodingTools(self.root)
        self.store = Store(Path(self.tmp.name) / 'state.sqlite')
        self.addCleanup(self.store.close)

    def run_patch(self, body, hook=None, tools=None):
        engine = Engine(self.store, tools or self.tools,
                        ScriptedModel([tool('apply_patch_text', {'patch': patch_text(body)}, 'patch_call'), final()]), hook)
        state = engine.run(engine.create('Apply described patch', Limits(max_seconds=60)))
        return state

    def test_multifile_patch_has_one_final_feedback_and_real_effects(self):
        (self.root / 'edit').write_text('one\ntwo\nthree\nfour\n')
        (self.root / 'delete').write_text('gone\n')
        state = self.run_patch('*** Add File: added\n+new\n*** Update File: edit\n@@ one\n-two\n+TWO\n@@\n three\n-four\n+FOUR\n*** End of File\n*** Delete File: delete')
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual((self.root / 'added').read_text(), 'new\n')
        self.assertEqual((self.root / 'edit').read_text(), 'one\nTWO\nthree\nFOUR\n')
        self.assertFalse((self.root / 'delete').exists())
        results = [m for m in state['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in results], ['patch_call'])
        result = json.loads(results[0]['content'])
        self.assertTrue(result['ok'])
        self.assertEqual([r['path'] for r in result['files']], ['added', 'edit', 'delete'])
        self.assertEqual(state['metrics']['tool_attempts'], 1)

    def test_later_context_conflict_prevents_earlier_add(self):
        (self.root / 'edit').write_text('actual\n')
        state = self.run_patch('*** Add File: added\n+new\n*** Update File: edit\n@@\n-wrong\n+new')
        self.assertFalse((self.root / 'added').exists())
        result = json.loads(next(m['content'] for m in state['messages'] if m['role'] == 'tool'))
        self.assertEqual(result, {'ok': False, 'error': 'patch_conflict'})

    def test_move_then_delete_source_preserves_destination(self):
        (self.root / 'old').write_text('old\n')
        state = self.run_patch('*** Update File: old\n*** Move to: new\n@@\n-old\n+new')
        self.assertEqual(state['phase'], 'completed')
        self.assertFalse((self.root / 'old').exists())
        self.assertEqual((self.root / 'new').read_text(), 'new\n')

    def test_strict_ambiguous_whitespace_and_eof_conflicts(self):
        for source, body in [
            ('x\nx\n', '@@\n-x\n+y'),
            (' x\n', '@@\n-x\n+y'),
            ('x\nz\n', '@@\n-x\n+y\n*** End of File'),
        ]:
            with self.subTest(source=source, body=body):
                (self.root / 'edit').write_text(source)
                with self.assertRaises(ToolError) as error:
                    self.tools.prepare('apply_patch_text', {'patch': patch_text('*** Update File: edit\n' + body)})
                self.assertEqual(error.exception.code, 'patch_conflict')
                self.assertEqual((self.root / 'edit').read_text(), source)

    def test_guards_revalidate_whole_source_vector_before_first_effect(self):
        (self.root / 'edit').write_text('old\n')
        prepared = self.tools.prepare('apply_patch_text', {'patch': patch_text(
            '*** Add File: added\n+new\n*** Update File: edit\n@@\n-old\n+new')})
        (self.root / 'edit').write_text('outside writer\n')
        from agent_lab.tools import UncertainEffect
        with self.assertRaises(UncertainEffect):
            self.tools.execute(prepared['steps'][0], 3)
        self.assertFalse((self.root / 'added').exists())
        self.assertEqual((self.root / 'edit').read_text(), 'outside writer\n')

    def test_changed_later_file_pauses_with_acknowledged_prefix(self):
        (self.root / 'edit').write_text('old\n')
        def hook(event, state):
            if event == 'after_tool_effect' and state['pending'].get('patch_cursor') == 0:
                (self.root / 'edit').write_text('outside writer\n')
        state = self.run_patch('*** Add File: added\n+new\n*** Update File: edit\n@@\n-old\n+new', hook)
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertEqual(state['pending']['patch_cursor'], 1)
        self.assertTrue(state['pending']['patch_inflight'])
        self.assertEqual((self.root / 'added').read_text(), 'new\n')
        self.assertEqual((self.root / 'edit').read_text(), 'outside writer\n')
        self.assertFalse(any(m['role'] == 'tool' for m in state['messages']))

    def test_move_does_not_remove_source_if_destination_changes(self):
        (self.root / 'old').write_text('old\n')
        def hook(event, state):
            if event == 'after_tool_effect' and state['pending'].get('patch_cursor') == 0:
                (self.root / 'new').write_text('outside writer\n')
        state = self.run_patch('*** Update File: old\n*** Move to: new\n@@\n-old\n+new', hook)
        self.assertEqual(state['phase'], 'needs_confirmation')
        self.assertEqual((self.root / 'old').read_text(), 'old\n')
        self.assertEqual((self.root / 'new').read_text(), 'outside writer\n')

    def test_paths_aliases_readonly_and_destinations_fail_before_effect(self):
        (self.root / 'old').write_text('old\n')
        cases = [
            ('*** Add File: ../escape\n+x', 'permission_denied'),
            ('*** Add File: .hidden\n+x', 'permission_denied'),
            ('*** Add File: old\n+x', 'patch_destination_exists'),
            ('*** Add File: A\n+x\n*** Add File: a\n+y', 'patch_path_conflict'),
            ('*** Add File: é\n+x\n*** Add File: é\n+y', 'patch_path_conflict'),
            ('*** Update File: old\n*** Move to: OLD\n@@\n-old\n+new', 'patch_path_conflict'),
            ('*** Add File: absent/new\n+x', 'parent_not_found'),
        ]
        for body, want in cases:
            with self.subTest(body=body), self.assertRaises(ToolError) as error:
                self.tools.prepare('apply_patch_text', {'patch': patch_text(body)})
            self.assertEqual(error.exception.code, want)
        protected = CodingTools(self.root, read_only_paths=['old'])
        for body in ['*** Delete File: old', '*** Update File: old\n@@\n-old\n+new',
                     '*** Add File: old\n+new',
                     '*** Add File: first\n+x\n*** Update File: old\n*** Move to: new\n@@\n-old\n+new']:
            with self.subTest(body=body), self.assertRaises(ToolError) as error:
                protected.prepare('apply_patch_text', {'patch': patch_text(body)})
            self.assertEqual(error.exception.code, 'read_only_path')
        self.assertEqual((self.root / 'old').read_text(), 'old\n')
        self.assertFalse((self.root / 'first').exists())

    def test_grammar_caps_and_old_tool_are_independent(self):
        for text in ['', '*** Begin Patch\n*** End Patch',
                     patch_text('*** Update File: old\n-old\n+new'),
                     patch_text('*** Add File: a\ninvalid'),
                     patch_text('*** Update File: old\n@@\n*** End of File'),
                     'x' * 65537]:
            with self.subTest(text=text[:100]), self.assertRaises(ToolError):
                self.tools.prepare('apply_patch_text', {'patch': text})
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('apply_patch_text', {'patch': patch_text('\n'.join(
                '*** Add File: f%d\n+x' % i for i in range(17)))})
        self.assertEqual(error.exception.code, 'patch_too_many_files')
        (self.root / 'old').write_text('x' * 65537)
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('apply_patch_text', {'patch': patch_text('*** Delete File: old')})
        self.assertEqual(error.exception.code, 'file_too_large')
        (self.root / 'old').write_text('old')
        result = self.tools.execute(self.tools.prepare('apply_patch', {'path': 'old', 'old': 'old', 'new': 'legacy'}), 3)
        self.assertTrue(result['ok'])
        self.assertEqual((self.root / 'old').read_text(), 'legacy')

    def test_patch_preserves_unterminated_file_and_rejects_crlf(self):
        (self.root / 'edit').write_bytes(b'a\nb')
        self.run_patch('*** Update File: edit\n@@ a\n-b\n+B')
        self.assertEqual((self.root / 'edit').read_bytes(), b'a\nB')
        (self.root / 'edit').write_bytes(b'a\r\nb\r\n')
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('apply_patch_text', {'patch': patch_text('*** Update File: edit\n@@\n-b\n+B')})
        self.assertEqual(error.exception.code, 'patch_line_endings_unsupported')

    def test_timeout_between_steps_reports_committed_prefix(self):
        def hook(event, state):
            if event == 'after_tool_effect':
                state['deadline'] = time.time() - 1
        state = self.run_patch('*** Add File: first\n+x\n*** Add File: second\n+y', hook)
        results = [json.loads(m['content']) for m in state['messages'] if m['role'] == 'tool']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['error'], 'tool_timeout')
        self.assertTrue(results[0]['partial'])
        self.assertEqual([r['path'] for r in results[0]['files']], ['first'])
        self.assertEqual(results[0]['remaining_steps'], 1)
        self.assertTrue((self.root / 'first').exists())
        self.assertFalse((self.root / 'second').exists())

    def test_sigkill_after_first_effect_fresh_process_reconciles_without_rewrite(self):
        body = patch_text('*** Add File: first\n+x\n*** Add File: second\n+y')
        database = Path(self.tmp.name) / 'crash.sqlite'
        proc = subprocess.Popen([sys.executable, '-c', CRASH_DRIVER, str(self.root), str(database), 'crash', body],
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdout, selectors.EVENT_READ)
                self.assertTrue(selector.select(15), 'child did not reach first effect')
            line = proc.stdout.readline()
            self.assertTrue(line, proc.stderr.read() if proc.poll() is not None else 'no ready event')
            receipt = json.loads(line)
            first = (self.root / 'first').stat()
            self.assertFalse((self.root / 'second').exists())
            with Store(database) as store:
                pending = store.load(receipt['id'])['pending']
                self.assertEqual(pending['patch_cursor'], 0)
                self.assertTrue(pending['patch_inflight'])
                self.assertEqual(pending['patch_receipts'], [])
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=8)
            self.assertEqual(proc.returncode, -signal.SIGKILL)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=8)
            proc.stdout.close()
            proc.stderr.close()
        resumed = subprocess.run([sys.executable, '-c', CRASH_DRIVER, str(self.root), str(database),
                                  'resume', body, receipt['id']], text=True, capture_output=True, timeout=15)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        result = json.loads(resumed.stdout)
        self.assertNotEqual(result['pid'], receipt['pid'])
        state = result['state']
        self.assertEqual(state['phase'], 'completed')
        after = (self.root / 'first').stat()
        self.assertEqual((first.st_ino, first.st_mtime_ns), (after.st_ino, after.st_mtime_ns))
        self.assertEqual((self.root / 'second').read_text(), 'y\n')
        messages = [json.loads(m['content']) for m in state['messages'] if m['role'] == 'tool']
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0]['files'][0]['reconciled'])
        self.assertFalse(messages[0]['files'][1]['reconciled'])
        self.assertEqual(state['metrics']['recoveries'], 1)

    def test_storage_failure_does_not_publish_cursor_or_replay_acknowledged_prefix(self):
        engine = Engine(self.store, self.tools, ScriptedModel([
            tool('apply_patch_text', {'patch': patch_text('*** Add File: first\n+x\n*** Add File: second\n+y')}, 'patch_call'), final()]))
        task_id = engine.create('Apply patch')
        save = self.store.save
        def fail(state, kind, meta=None, **kwargs):
            if kind == 'patch_step_finished':
                raise StorageUnavailable('injected')
            return save(state, kind, meta, **kwargs)
        with patch.object(self.store, 'save', side_effect=fail), self.assertRaises(StorageUnavailable):
            engine.run(task_id)
        first = (self.root / 'first').stat()
        pending = self.store.load(task_id)['pending']
        self.assertEqual(pending['patch_cursor'], 0)
        self.assertTrue(pending['patch_inflight'])
        resumed = Engine(self.store, self.tools, ScriptedModel([final()])).run(task_id)
        self.assertEqual(resumed['phase'], 'completed')
        self.assertEqual((self.root / 'first').stat().st_ino, first.st_ino)
        self.assertTrue((self.root / 'second').exists())

    def test_corrupt_cursor_cannot_skip_unapplied_file(self):
        engine = Engine(self.store, self.tools, ScriptedModel([
            tool('apply_patch_text', {'patch': patch_text('*** Add File: first\n+x\n*** Add File: second\n+y')}, 'patch_call')]))
        task_id = engine.create('Apply patch')
        def stop(event, state):
            if event == 'after_tool_effect':
                raise KeyboardInterrupt()
        engine.hook = stop
        state = engine.run(task_id)
        # Model local-state corruption, not an authorized user edit.
        state['phase'] = 'tool_pending'
        state['pending']['patch_cursor'] = 1
        self.store.save(state, 'synthetic_corruption')
        resumed = Engine(self.store, self.tools, ScriptedModel([final()])).run(task_id)
        self.assertEqual(resumed['phase'], 'needs_confirmation')
        self.assertFalse((self.root / 'second').exists())

    def test_recovery_at_deadline_reconciles_current_then_reports_unstarted_suffix(self):
        def stop(event, state):
            if event == 'after_tool_effect':
                raise KeyboardInterrupt()
        state = self.run_patch('*** Add File: first\n+x\n*** Add File: second\n+y', stop)
        state['phase'] = 'tool_pending'
        state['deadline'] = time.time() - 1
        self.store.save(state, 'synthetic_expiry')
        resumed = Engine(self.store, self.tools, ScriptedModel([])).run(state['id'])
        results = [json.loads(m['content']) for m in resumed['messages'] if m['role'] == 'tool']
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]['partial'])
        self.assertTrue(results[0]['files'][0]['reconciled'])
        self.assertEqual(results[0]['remaining_steps'], 1)
        self.assertFalse((self.root / 'second').exists())

    def test_move_recovery_reconciles_destination_before_removing_source(self):
        (self.root / 'old').write_text('old\n')
        def stop(event, state):
            if event == 'after_tool_effect':
                raise KeyboardInterrupt()
        state = self.run_patch('*** Update File: old\n*** Move to: new\n@@\n-old\n+new', stop)
        self.assertTrue((self.root / 'old').exists())
        target = (self.root / 'new').stat()
        state['phase'] = 'tool_pending'
        self.store.save(state, 'synthetic_resume')
        resumed = Engine(self.store, self.tools, ScriptedModel([final()])).run(state['id'])
        self.assertEqual(resumed['phase'], 'completed')
        self.assertFalse((self.root / 'old').exists())
        self.assertEqual((self.root / 'new').stat().st_ino, target.st_ino)

    def test_small_patch_over_large_file_vector_has_bounded_prepared_transport(self):
        for i in range(6):
            (self.root / ('f%d' % i)).write_bytes(b'\x01' * 65530 + b'\nx\n')
        text = patch_text('\n'.join('*** Update File: f%d\n@@\n-x\n+y' % i for i in range(6)))
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('apply_patch_text', {'patch': text})
        self.assertEqual(error.exception.code, 'patch_intent_too_large')
        self.assertTrue(all((self.root / ('f%d' % i)).read_bytes().endswith(b'x\n') for i in range(6)))

    def test_patch_result_precedes_following_call_in_same_model_batch(self):
        request = tool('apply_patch_text', {'patch': patch_text('*** Add File: first\n+x\n*** Add File: second\n+y')}, 'patch_call')
        request['message']['tool_calls'].extend(tool('read_file', {'path': 'second'}, 'read_call')['message']['tool_calls'])
        model = ScriptedModel([request, final()])
        engine = Engine(self.store, self.tools, model)
        state = engine.run(engine.create('Patch then read'))
        self.assertEqual(state['phase'], 'completed')
        results = [m for m in state['messages'] if m['role'] == 'tool']
        self.assertEqual([m['tool_call_id'] for m in results], ['patch_call', 'read_call'])
        self.assertEqual(json.loads(results[1]['content'])['content'], 'y\n')
        self.assertEqual(state['metrics']['tool_attempts'], 2)
        self.assertEqual(len(model.seen), 2)

    def test_readonly_destination_and_symlink_preflight_and_recovery(self):
        (self.root / 'old').write_text('old\n')
        (self.root / 'link').symlink_to(self.root / 'old')
        with self.assertRaises(ToolError) as error:
            self.tools.prepare('apply_patch_text', {'patch': patch_text('*** Delete File: link')})
        self.assertEqual(error.exception.code, 'permission_denied')
        text = patch_text('*** Update File: old\n*** Move to: new\n@@\n-old\n+new')
        protected = CodingTools(self.root, read_only_paths=['new'])
        with self.assertRaises(ToolError) as error:
            protected.prepare('apply_patch_text', {'patch': text})
        self.assertEqual(error.exception.code, 'read_only_path')
        intent = self.tools.prepare('apply_patch_text', {'patch': text})
        from agent_lab.tools import UncertainEffect
        with self.assertRaises((ToolError, UncertainEffect)):
            protected.recover(intent['steps'][0], 3)
        self.assertTrue((self.root / 'old').exists())
        self.assertFalse((self.root / 'new').exists())

    def test_insertion_empty_add_and_multiple_ordered_chunks(self):
        (self.root / 'edit').write_text('a\nb\nc\n')
        state = self.run_patch('*** Add File: empty\n*** Update File: edit\n@@ a\n+insert\n@@\n-b\n+B\n@@\n+tail\n*** End of File')
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual((self.root / 'empty').read_bytes(), b'')
        self.assertEqual((self.root / 'edit').read_text(), 'a\ninsert\nB\nc\ntail\n')


if __name__ == '__main__':
    unittest.main()
