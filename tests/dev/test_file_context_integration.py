"""Opted-in file-version context, with scripted samples and real file bytes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_lab.coding_tools import CodingTools
from agent_lab.context import build_context
from agent_lab.engine import Engine, Limits
from agent_lab.store import Store, StorageUnavailable
from agent_lab.tools import Tools
from tests.support import ScriptedModel, tool, final


def sha(data): return hashlib.sha256(data).hexdigest()


class FileContextIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / 'work'; self.root.mkdir()
        self.path = self.root / 'source.py'; self.path.write_bytes(b'OLD_PRIVATE_VALUE\n')
        self.tools = CodingTools(self.root)
        self.store = Store(self.root.parent / 'state.sqlite'); self.addCleanup(self.store.close)

    def notices(self, state): return [m for m in state['messages'] if '_file_context' in m]
    def payload(self, notice): return json.loads(notice['content'].split('\n', 1)[1])

    def test_read_file_sha_is_from_exact_returned_bytes_without_second_read(self):
        original = self.tools._bytes; seen = []
        def switch(path):
            data = original(path); seen.append(path); self.path.write_bytes(b'NEW_PRIVATE_VALUE\n'); return data
        with patch.object(self.tools, '_bytes', side_effect=switch):
            result = self.tools._execute(self.tools._prepare('read_file', {'path': 'source.py'}), 2)
        self.assertEqual(result['content'], 'OLD_PRIVATE_VALUE\n')
        self.assertEqual(result['sha256'], sha(b'OLD_PRIVATE_VALUE\n'))
        self.assertEqual(len(seen), 1)

    def test_opt_in_record_does_not_fabricate_default_or_local_profile_reads(self):
        model = ScriptedModel([final()]); engine = Engine(self.store, self.tools, model)
        default = engine.create('Default behavior')
        self.assertNotIn('file_context', self.store.load(default))
        enabled = engine.create('Track future reads', track_file_context=True)
        record = self.store.load(enabled)['file_context']
        self.assertEqual(record, {'version': 1, 'reads': [], 'coverage_limited': False, 'last_snapshot': None})
        with self.assertRaises(ValueError): Engine(self.store, Tools(self.root), model).create('Local', track_file_context=True)
        with self.assertRaises(ValueError): engine.create('Bad flag', track_file_context=1)

    def test_external_same_size_mtime_change_is_not_a_new_read_and_precedes_sampling(self):
        before = self.path.stat()
        def edit(kind, state):
            if kind == 'after_tool_effect':
                self.path.write_bytes(b'NEW_PRIVATE_VALUE\n')
                os.utime(self.path, ns=(before.st_atime_ns, before.st_mtime_ns))
        model = ScriptedModel([tool('read_file', {'path': 'source.py'}), final()])
        engine = Engine(self.store, self.tools, model, hook=edit)
        tid = engine.create('Read source', track_file_context=True); state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        notes = self.notices(state); self.assertEqual(len(notes), 1)
        item = self.payload(notes[0])['files'][0]
        self.assertEqual(item['status'], 'changed')
        self.assertEqual(item['read_sha256'], sha(b'OLD_PRIVATE_VALUE\n'))
        self.assertEqual(item['current_sha256'], sha(b'NEW_PRIVATE_VALUE\n'))
        self.assertEqual(state['file_context']['reads'][0]['sha256'], item['read_sha256'])
        self.assertIn({'role': 'user', 'content': notes[0]['content']}, model.seen[-1])
        self.assertFalse(any('_file_context' in m for m in model.seen[-1]))
        original_result = json.loads(state['messages'][state['file_context']['reads'][0]['message_index']]['content'])
        self.assertEqual(original_result['content'], 'OLD_PRIVATE_VALUE\n')
        metadata = json.dumps(self.store.events(tid))
        self.assertNotIn('source.py', metadata); self.assertNotIn('PRIVATE_VALUE', metadata)

    def test_true_reread_updates_baseline_and_latest_view_supersedes_notice_history(self):
        edits = []
        def edit(kind, state):
            if kind == 'after_tool_effect' and not edits:
                edits.append(True); self.path.write_bytes(b'NEW_PRIVATE_VALUE\n')
        model = ScriptedModel([tool('read_file', {'path': 'source.py'}),
                               tool('read_lines', {'path': 'source.py', 'start_line': 1, 'end_line': 1}), final()])
        engine = Engine(self.store, self.tools, model, hook=edit)
        tid = engine.create('Read and re-read', track_file_context=True); state = engine.run(tid)
        notes = self.notices(state); self.assertEqual(len(notes), 2)
        self.assertEqual(self.payload(notes[0])['files'][0]['status'], 'changed')
        self.assertEqual(self.payload(notes[1])['files'][0]['status'], 'same')
        self.assertEqual(state['file_context']['reads'][0]['sha256'], sha(b'NEW_PRIVATE_VALUE\n'))
        before = copy.deepcopy(state['messages'])
        view, _ = build_context(state['messages'], self.tools.schemas(), 262144)
        self.assertNotIn(notes[0]['content'], [m.get('content') for m in view])
        self.assertIn(notes[1]['content'], [m.get('content') for m in view])
        self.assertEqual(state['messages'], before)
        self.assertFalse(any('_file_context' in m for m in view))

    def test_continuation_dedupes_and_fork_rechecks_shared_disk_without_mutating_source(self):
        model = ScriptedModel([tool('read_file', {'path': 'source.py'}), final()])
        engine = Engine(self.store, self.tools, model)
        tid = engine.create('First read', track_file_context=True); engine.run(tid)
        self.path.write_bytes(b'NEW_PRIVATE_VALUE\n')
        engine.model = ScriptedModel([final()]); engine.continue_turn(tid, 'Continue'); first = engine.run(tid)
        self.assertEqual(len(self.notices(first)), 1)
        engine.model = ScriptedModel([final()]); engine.continue_turn(tid, 'Again'); second = engine.run(tid)
        self.assertEqual(len(self.notices(second)), 1)
        source = copy.deepcopy(second)
        child_id = engine.fork_session(tid, 'Alternative')
        self.assertEqual(self.store.load(child_id)['file_context'], second['file_context'])
        self.path.write_bytes(b'THIRD_PRIVATE_DATA\n')
        engine.model = ScriptedModel([final()]); child = engine.run(child_id)
        self.assertEqual(len(self.notices(child)), 2)
        self.assertEqual(self.store.load(tid), source)
        self.assertEqual(child['file_context']['reads'][0]['sha256'], sha(b'OLD_PRIVATE_VALUE\n'))

    def test_after_commit_save_failure_retries_notice_not_read_or_model(self):
        def edit(kind, state):
            if kind == 'after_tool_effect': self.path.write_bytes(b'NEW_PRIVATE_VALUE\n')
        original = self.store.save; failed = []
        def save(state, kind, meta=None, **kwargs):
            result = original(state, kind, meta, **kwargs)
            if kind == 'file_context_refreshed' and not failed:
                failed.append(True); raise StorageUnavailable('storage_unavailable')
            return result
        model = ScriptedModel([tool('read_file', {'path': 'source.py'}), final()])
        engine = Engine(self.store, self.tools, model, hook=edit)
        tid = engine.create('Read', track_file_context=True)
        with patch.object(self.store, 'save', side_effect=save): state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed'); self.assertEqual(failed, [True])
        self.assertEqual(len(self.notices(state)), 1)
        self.assertEqual(len([e for e in self.store.events(tid) if e['kind'] == 'file_context_refreshed']), 1)
        self.assertEqual(state['metrics']['tool_attempts'], 1); self.assertEqual(len(model.seen), 2)

    def test_failed_notice_commit_prevents_sampling_and_safe_retry_emits_once(self):
        def edit(kind, state):
            if kind == 'after_tool_effect': self.path.write_bytes(b'NEW_PRIVATE_VALUE\n')
        original = self.store.save
        def unavailable(state, kind, meta=None, **kwargs):
            if kind == 'file_context_refreshed': raise StorageUnavailable('storage_unavailable')
            return original(state, kind, meta, **kwargs)
        model = ScriptedModel([tool('read_file', {'path': 'source.py'}), final()])
        engine = Engine(self.store, self.tools, model, hook=edit)
        tid = engine.create('Read', track_file_context=True)
        with patch.object(self.store, 'save', side_effect=unavailable), self.assertRaises(StorageUnavailable): engine.run(tid)
        stored = self.store.load(tid)
        self.assertEqual(stored['phase'], 'ready'); self.assertEqual(self.notices(stored), [])
        self.assertIsNone(stored['file_context']['last_snapshot'])
        self.assertEqual(len(model.seen), 1)
        state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed'); self.assertEqual(len(self.notices(state)), 1)
        self.assertEqual(len(model.seen), 2)

    def test_semantic_prefix_and_copied_archive_stay_immutable_after_new_observation(self):
        from agent_lab.compaction import completed_record, source_hash, validate_record
        model = ScriptedModel([tool('read_file', {'path': 'source.py'}), final()])
        engine = Engine(self.store, self.tools, model)
        tid = engine.create('First', track_file_context=True); before = engine.run(tid)
        end = len(before['messages']); prefix = copy.deepcopy(before['messages'])
        record = completed_record(prefix, end, {**final('Synthetic summary only'), 'finish_reason': 'stop'}, model.identity, None)
        before['semantic_context'] = record; self.store.save(before, 'fixture')
        self.path.write_bytes(b'NEW_PRIVATE_VALUE\n')
        engine.model = ScriptedModel([final()]); engine.continue_turn(tid, 'Continue'); state = engine.run(tid)
        self.assertEqual(state['phase'], 'completed')
        self.assertEqual(state['messages'][:end], prefix)
        self.assertEqual(source_hash(state['messages'], end), record['source_sha256'])
        self.assertEqual(validate_record(state['messages'], record, model.identity), end)
        self.assertEqual(len(self.notices(state)), 1)


if __name__ == '__main__': unittest.main()
